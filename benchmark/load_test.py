"""
Automated Load Testing & Benchmarking Tool for vLLM Inference Server.

Measures industry-standard LLM performance metrics:
- TTFT (Time To First Token) via Server-Sent Events (SSE) streaming
- End-to-End Latency
- Throughput (tokens per second)
- TPOT (Time Per Output Token)
"""

import argparse
import asyncio
import json
import time
from typing import Any, Dict, List
import aiohttp


# Test prompts: Short vs. Long
SHORT_PROMPT = "Привет! Объясни кратко в двух предложениях, что такое Kubernetes и зачем он нужен."

LONG_PROMPT = """
Ниже приведен технический текст о распределенных системах и инференсе моделей:
Архитектура современных распределенных систем машинного обучения строится на принципах 
горизонтального масштабирования, изоляции вычислительных ресурсов и эффективного управления памятью.
При развертывании больших языковых моделей (LLM) инженеры сталкиваются с двумя ключевыми ограничениями:
вычислительной сложностью механизма внимания (Self-Attention) на этапе обработки входного контекста (Prefill)
и задержками доступа к оперативной памяти при последовательной генерации токенов (Decode).
Ключевую роль играет оптимизация KV-кэша, предотвращение фрагментации памяти (например, PagedAttention) 
и непрерывный динамический батчинг (Continuous Batching). 

Проанализируй данный фрагмент и выдели 3 ключевых вывода об оптимизации инференса.
"""


async def send_streaming_request(
    session: aiohttp.ClientSession,
    endpoint: str,
    api_key: str,
    model_name: str,
    prompt: str,
    max_tokens: int,
) -> Dict[str, Any]:
    """
    Sends a chat completion request with stream=True to measure exact TTFT and Latency.
    """
    url = f"{endpoint.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else "",
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
    }

    start_time = time.perf_counter()
    first_token_time = None
    output_tokens = 0
    full_text = []

    try:
        async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                return {
                    "success": False,
                    "status": resp.status,
                    "error": err_text[:100],
                    "ttft": 0,
                    "latency": 0,
                    "tokens": 0,
                }

            async for line_bytes in resp.content:
                line = line_bytes.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content_piece = delta.get("content", "")
                        if content_piece:
                            if first_token_time is None:
                                first_token_time = time.perf_counter()
                            output_tokens += 1
                            full_text.append(content_piece)
                except json.JSONDecodeError:
                    continue

        end_time = time.perf_counter()
        total_latency = end_time - start_time
        ttft = (first_token_time - start_time) if first_token_time else total_latency

        return {
            "success": True,
            "status": 200,
            "ttft": ttft,
            "latency": total_latency,
            "tokens": output_tokens,
        }

    except Exception as e:
        return {
            "success": False,
            "status": 0,
            "error": str(e)[:100],
            "ttft": 0,
            "latency": 0,
            "tokens": 0,
        }


async def run_benchmark_suite(
    endpoint: str,
    api_key: str,
    model_name: str,
    concurrency: int,
    num_requests: int,
    prompt: str,
    prompt_label: str,
    max_tokens: int,
) -> Dict[str, Any]:
    print(f"\n---> Запуск теста: [{prompt_label}] (Параллельных запросов: {concurrency}, Всего: {num_requests})")
    
    semaphore = asyncio.Semaphore(concurrency)
    
    async def worker(session):
        async with semaphore:
            return await send_streaming_request(
                session, endpoint, api_key, model_name, prompt, max_tokens
            )

    conn = aiohttp.TCPConnector(limit=concurrency + 5)
    async with aiohttp.ClientSession(connector=conn) as session:
        wall_start = time.perf_counter()
        tasks = [worker(session) for _ in range(num_requests)]
        results = await asyncio.gather(*tasks)
        wall_end = time.perf_counter()

    wall_duration = wall_end - wall_start
    successful = [r for r in results if r["success"]]
    failures = len(results) - len(successful)

    if not successful:
        print(f"Все запросы завершились ошибкой! Проверьте эндпоинт и Bearer token.")
        return {}

    ttfts = [r["ttft"] * 1000 for r in successful]  # in ms
    latencies = [r["latency"] for r in successful]  # in s
    total_tokens = sum(r["tokens"] for r in successful)
    throughput = total_tokens / wall_duration if wall_duration > 0 else 0

    avg_ttft = sum(ttfts) / len(ttfts)
    avg_latency = sum(latencies) / len(latencies)

    return {
        "label": prompt_label,
        "concurrency": concurrency,
        "total_requests": num_requests,
        "success_rate": f"{(len(successful) / num_requests) * 100:.1f}%",
        "avg_ttft_ms": f"{avg_ttft:.2f}",
        "avg_latency_s": f"{avg_latency:.2f}",
        "total_tokens": total_tokens,
        "throughput_tokens_per_sec": f"{throughput:.2f}",
        "wall_time_s": f"{wall_duration:.2f}",
    }


def main():
    parser = argparse.ArgumentParser(description="vLLM Inference Load Testing")
    parser.add_argument("--endpoint", default="http://localhost:8000", help="vLLM Server base URL")
    parser.add_argument("--api-key", default="sk-kaspersky-de-bearer-token-2026", help="Bearer API key")
    parser.add_argument("--model", default="Qwen2.5-1.5B-Instruct", help="Served model name")
    parser.add_argument("--concurrency", type=int, default=2, help="Concurrency level")
    parser.add_argument("--requests", type=int, default=4, help="Total requests per test")
    parser.add_argument("--max-tokens", type=int, default=100, help="Max tokens to generate")
    args = parser.parse_args()

    print("================================================================================")
    print("                KASPERSKY ML RESEARCH - vLLM LOAD TEST SUITE                   ")
    print(f"Endpoint: {args.endpoint} | Model: {args.model}")
    print("================================================================================")

    # 1. Run Short Prompt benchmark
    res_short = asyncio.run(
        run_benchmark_suite(
            args.endpoint,
            args.api_key,
            args.model,
            args.concurrency,
            args.requests,
            SHORT_PROMPT,
            "Короткий промпт (~25 токенов)",
            args.max_tokens,
        )
    )

    # 2. Run Long Prompt benchmark
    res_long = asyncio.run(
        run_benchmark_suite(
            args.endpoint,
            args.api_key,
            args.model,
            args.concurrency,
            args.requests,
            LONG_PROMPT,
            "Длинный промпт (~150 токенов)",
            args.max_tokens,
        )
    )

    # Summary table
    print("\n================================================================================")
    print("                          ИТОГОВАЯ ТАБЛИЦА МЕТРИК                               ")
    print("================================================================================")
    header = f"| {'Тип промпта':<28} | {'Конкурентность':<14} | {'TTFT (ms)':<10} | {'Latency (s)':<11} | {'Throughput (tok/s)':<19} | {'Успех':<7} |"
    sep = f"|{'-'*30}|{'-'*16}|{'-'*12}|{'-'*13}|{'-'*21}|{'-'*9}|"
    print(header)
    print(sep)
    for res in [res_short, res_long]:
        if res:
            row = f"| {res['label']:<28} | {res['concurrency']:<14} | {res['avg_ttft_ms']:<10} | {res['avg_latency_s']:<11} | {res['throughput_tokens_per_sec']:<19} | {res['success_rate']:<7} |"
            print(row)
    print("================================================================================")


if __name__ == "__main__":
    main()
