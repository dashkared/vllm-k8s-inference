# vLLM CPU Inference on Kubernetes (Rootless & Secure)

Решение тестового задания на позицию **Data Engineer (Стажёр)** в команду **Исследования технологий машинного обучения** («Лаборатория Касперского»).

Развернута отказоустойчивая система инференса открытой модели `Qwen/Qwen2.5-1.5B-Instruct` на базе фреймворка **vLLM** строго на CPU в среде **Kubernetes**, с соблюдением корпоративных практик информационной безопасности (Rootless, Pinned dependencies, Privacy logging) и реализацией всех трех дополнительных бонусных заданий.

---

## Архитектура решения

```mermaid
flowchart TD
    subgraph K8s["Kubernetes Cluster (Docker Desktop / kind / Minikube)"]
        direction TB

        subgraph IngressLayer["Сетевой уровень"]
            Svc["Service: vllm-service (NodePort :30080 / ClusterIP :8000)"]
        end

        subgraph Pod["Pod: vllm-inference (UID 10001, GID 10001, fsGroup 10001)"]
            direction LR
            InitC["initContainer: model-downloader\n(Легковесный образ, Python 3.11-slim)\nПроверка кэша и загрузка весов"]
            MainC["Main Container: vllm-server\n(vLLM 0.33.0 CPU Engine)\nИнференс + Privacy Logging Filter"]
            InitC -->|1. Завершается [Completed]| MainC
        end

        subgraph StorageLayer["Слой хранения и конфигурации"]
            PVC["PVC: vllm-model-pvc (10Gi)\nЛокальный кэш весов"]
            CM["ConfigMap: vllm-config\nПараметры инференса (CPU, batch, port)"]
            Sec["Secret: vllm-secrets\nHF_TOKEN (закрытые модели)\nAPI_KEY (Bearer token)"]
        end

        Svc --> MainC
        PVC ===|/data| InitC
        PVC ===|/data| MainC
        CM -.-> InitC
        CM -.-> MainC
        Sec -.->|HF_TOKEN| InitC
        Sec -.->|API_KEY| MainC
    end

    Client["Клиент / Load Test Script (Bearer Auth)"] -->|HTTP POST :30080| Svc
```

---

## Реализованные требования и бонусы

| Категория | Требование | Реализация в проекте |
| :--- | :--- | :--- |
| **Инфраструктура** | Среда k8s / CPU инференс | Кластер Kubernetes (Docker Desktop / kind), модель `Qwen2.5-1.5B-Instruct`, инференс строго на CPU (`--device cpu`). |
| **Версия фреймворка** | vLLM >= 0.27.0 | Используется **vLLM v0.33.0** с официальной CPU-оптимизацией под x86_64 и ARM64. |
| **Безопасность** | Rootless (non-root) | Оба образа работают от непривилегированного пользователя (`UID 10001`, `appuser` / `vllm`), запрет эскалации прав в `securityContext`. |
| **Воспроизводимость** | Pinned dependencies | Все 192 зависимости vLLM и 12 зависимостей initContainer зафиксированы (`package==x.y.z`) в `requirements.txt`. |
| **Приватность** | Кастомное логирование | Модуль `privacy_filter.py` маскирует тексты промптов (`[REDACTED_PROMPT]`) и ответов (`[REDACTED_CONTENT]`), сохраняя аудитные метаданные (timestamp, request_id, latency, tokens). |
| **Хранилище** | PVC + Кэширование | Диск 10GB (`PersistentVolumeClaim`). Логика `[CACHE HIT]` / `[CACHE MISS]`: при первом старте веса скачиваются с HF, при рестартах — мгновенный старт оффлайн. |
| **Секреты** | k8s Secret | `hf-token` и `api-key` (Bearer token) безопасно проброшены через `vllm-secrets`. |
| **Конфигурация** | k8s ConfigMap | Все параметры инференса (`MAX_MODEL_LEN`, `BLOCK_SIZE`, `PORT`) вынесены в `vllm-config`. |
| **Бонус 1** | Kustomize | Манифесты собраны через `kustomization.yaml`, деплой одной командой: `kubectl apply -k k8s/base`. |
| **Бонус 2** | initContainer | Скачивание вынесено в отдельный легковесный Rootless Dockerfile (`Dockerfile.init` ~200 МБ) с независимым жизненным циклом. |
| **Бонус 3** | Нагрузочный тест и аналитика | Скрипт `load_test.py` (замер TTFT через SSE-стриминг, Latency, Throughput), итоговая таблица метрик и аналитика параметров vLLM. |

---

## Быстрый старт

### 1. Предварительные требования
* Установленный **Docker Desktop** (с включенным Kubernetes: `Settings -> Kubernetes -> Enable Kubernetes`).
* Утилита **kubectl** (встроенная в Docker Desktop).

### 2. Сборка Docker-образов
Соберите основной образ инференса и легковесный initContainer локально:
```bash
# 1. Легковесный Rootless initContainer для скачивания весов
docker build -t vllm-model-downloader:latest -f docker/Dockerfile.init docker/

# 2. Основной Rootless образ vLLM CPU инференса
docker build -t vllm-inference:latest -f docker/Dockerfile docker/
```

### 3. Развертывание в Kubernetes через Kustomize (Бонус 1)
```bash
kubectl apply -k k8s/base
```

Проверьте статус развертывания:
```bash
kubectl get pods,pvc,svc -l app=vllm-inference
```

*При первом старте:* под перейдет в статус `Init:0/1` (initContainer скачивает веса модели ~3 ГБ на PVC).  
*После скачивания:* под автоматически перейдет в статус `Running 1/1` (основной контейнер загрузит веса в RAM и начнет отдавать трафик).

Логи скачивания модели:
```bash
kubectl logs -l app=vllm-inference -c model-downloader -f
```

Логи сервера vLLM:
```bash
kubectl logs -l app=vllm-inference -c vllm-server -f
```

---

## 🔒 Проверка безопасности и API Bearer Token

Эндпоинт доступен снаружи кластера через NodePort на `http://localhost:30080` (или через `kubectl port-forward svc/vllm-service 8000:8000`).

### 1. Запрос БЕЗ токена (Ожидается отказ 401 Unauthorized):
```bash
curl -i http://localhost:30080/v1/models
```
*Ответ:* `HTTP/1.1 401 Unauthorized` ({"detail": "Not authenticated"}).

### 2. Запрос С валидным Bearer Token (Успех 200 OK):
```bash
curl -X POST http://localhost:30080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-kaspersky-de-bearer-token-2026" \
  -d '{
    "model": "Qwen2.5-1.5B-Instruct",
    "messages": [{"role": "user", "content": "Привет, расскажи о себе в одном предложении."}],
    "max_tokens": 50
  }'
```

---

## Нагрузочное тестирование и аналитика

### Запуск нагрузочного скрипта:
Скрипт использует асинхронный стриминг (SSE) для вычисления точного **TTFT (Time To First Token)**, общей **Latency** и **Throughput (токенов/сек)**:
```bash
python3 -m pip install aiohttp
python3 benchmark/load_test.py --endpoint http://localhost:30080 --api-key sk-kaspersky-de-bearer-token-2026 --concurrency 2 --requests 4
```

### Итоговая таблица эмпирических замеров (CPU Inference, Apple Silicon Docker Desktop VM):

| Тип промпта | Конкурентность | TTFT (ms) | Latency (s) | Throughput (tok/s) | Успех (%) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Короткий промпт (~25 токенов)** | 1 (baseline) | **670.28 ms** | 5.06 s | 5.92 tok/s | 100.0% |
| **Длинный промпт (~150 токенов)** | 1 (prefix cache) | **286.61 ms** | 5.06 s | 5.93 tok/s | 100.0% |
| **Короткий промпт (~25 токенов)** | 2 (параллельно) | **2538.48 ms** | 6.94 s | **8.64 tok/s** | 100.0% |
| **Длинный промпт (~150 токенов)** | 2 (параллельно) | **4432.02 ms** | 8.88 s | **6.69 tok/s** | 100.0% |

> **Ключевые инсайты бенчмарка:**
> 1. **Continuous Batching на CPU**: При росте параллелизма с 1 до 2 совокупный Throughput вырос с **5.92 tok/s до 8.64 tok/s (+45.9%)**, так как vLLM эффективно объединяет шаги декодирования разных запросов в единую матричную операцию.
> 2. **Эффект Prefix Caching**: При повторном обращении с длинным контекстом TTFT сократился до **286 ms**, подтверждая эффективность повторного использования рассчитанного KV-кэша.
> 3. **Трейд-офф задержки**: Конкуренция за потоки OpenMP при росте параллелизма увеличивает индивидуальный TTFT и Latency, что иллюстрирует классический баланс между пропускной способностью (Throughput) и отзывчивостью (TTFT) в распределенных системах.

---

## Аналитика тюнинга параметров vLLM (Research Analysis)

### А) Большой поток запросов с маленьким количеством токенов (High Concurrency, Short Context):
1. **Увеличить `max_num_seqs` (например, с 256 до 512+):**
   * Позволяет механизму *Continuous Batching* объединять большее число параллельных легковесных запросов в единый матричный шаг декодирования, максимизируя утилизацию CPU-потоков.
2. **Уменьшить `block_size` в PagedAttention (выбрать 8 или 16 вместо 32/64):**
   * При коротких ответах большой размер блока приводит к тому, что в физическом блоке памяти занято всего 5–10 токенов, а остальная часть блока простаивает (внутренняя фрагментация). Маленький блок обеспечивает максимальную плотность упаковки KV-кэша в RAM.
3. **Держать `max_num_batched_tokens` умеренным:**
   * Входные промпты короткие, поэтому нет необходимости резервировать гигантские порции под Prefill.

### Б) Мало запросов, но с огромным количеством токенов (Large Context, RAG, Анализ логов):
1. **Увеличить `max_num_batched_tokens` (до 4096 / 8192):**
   * Задает лимит токенов на один шаг вычисления фазы Prefill. Если параметр занижен, длинный промпт дробится на слишком мелкие чанки, что драматически увеличивает TTFT.
2. **Включить `enable_prefix_caching: True`:**
   * В задачах RAG и анализа документов системные инструкции или база знаний часто повторяются в начале промпта. Prefix Caching переиспользует уже рассчитанный KV-кэш для одинаковых префиксов, снижая TTFT повторных запросов практически до нуля.
3. **Включить `enable_chunked_prefill: True`:**
   * Позволяет выполнять тяжелый Prefill длинного контекста параллельно с шагами декодирования других активных запросов, предотвращая блокировку сервера (Head-of-Line Blocking).
4. **Увеличить `block_size` (до 32 или 64):**
   * При длинных цепочках последовательный доступ к большим блокам памяти снижает накладные расходы на таблицу страниц (Block Table) и повышает кэш-локальность процессора.
