# vLLM CPU Inference on Kubernetes (Rootless & Secure)

Решение тестового задания на позицию **Data Engineer (Стажёр)** в команду **Исследования технологий машинного обучения** («Лаборатория Касперского»).

Развернута отказоустойчивая система инференса открытой модели `Qwen/Qwen2.5-1.5B-Instruct` на базе фреймворка **vLLM** строго на CPU в среде **Kubernetes**, с соблюдением корпоративных практик информационной безопасности (Rootless, Pinned dependencies, Privacy logging) и реализацией всех трех дополнительных бонусных заданий.

---

## Архитектура решения

### 1. Двухэтапная модель жизненного цикла пода (Pod Lifecycle)
* **Фаза инициализации (`initContainer: model-downloader`):**
  * Запускается в легковесном изолированном образе (~200 МБ на базе `python:3.11-slim`) от непривилегированного пользователя `UID 10001`.
  * Монтирует общий том постоянного хранилища `/data` из `PersistentVolumeClaim` (10Gi).
  * Выполняет скрипт `download_model.py`: проверяет целостность локальных снапшотов модели `Qwen/Qwen2.5-1.5B-Instruct`.
  * При «теплом» старте обнаруживает кэш (`[CACHE HIT]`), пропуская обращение к внешним сетям и завершаясь за секунду.
  * При «холодном» старте (`[CACHE MISS]`) скачивает веса из Hugging Face Hub (при необходимости передавая `HF_TOKEN` из Kubernetes Secret).
  * Завершается со статусом `Completed` и полностью освобождает вычислительные ресурсы ноды перед стартом инференса.
* **Фаза инференса (`Main Container: vllm-server`):**
  * Стартует только после успешного завершения `initContainer`.
  * Запускает высокопроизводительный движок vLLM v0.33.0 в режиме CPU-инференса от `UID 10001`.
  * Загружает веса модели локально из смонтированного каталога `/data/models/...` в оперативную память.
  * Аллоцирует KV-кэш через алгоритм PagedAttention с фиксированным лимитом `KV_CACHE_MEMORY_BYTES: 512M` (достаточно для ~18 700 токенов контекста).
  * Монтирует выделенный `emptyDir` (1Gi в RAM) в `/dev/shm` для разделяемой памяти и межпроцессного обмена.
  * Активирует кастомный фильтр логирования `PrivacyLogFilter`, маскирующий конфиденциальные данные пользователей (`[FILTERED]`).

### 2. Сетевой уровень и авторизация (Networking & Security)
* **Сервис `vllm-service`:**
  * Предоставляет стабильную точку входа `ClusterIP: 8000` внутри кластера и `NodePort: 30080` для внешнего доступа.
  * Перенаправляет трафик на порт 8000 пода инференса с проверкой состояния через Liveness, Readiness и Startup Probes (`/health`).
* **Защита эндпоинтов (Bearer Authentication):**
  * Все API-методы (`/v1/chat/completions`, `/v1/models`) защищены статическим токеном.
  * При неавторизованном запросе сервер немедленно возвращает `HTTP 401 Unauthorized`.

### 3. Хранение и декларативная конфигурация
* **`PersistentVolumeClaim: vllm-model-pvc` (10Gi, `ReadWriteOnce`):** сохраняет скачанные веса модели между рестартами подов, исключая повторную нагрузку на сеть.
* **`ConfigMap: vllm-config`:** централизованно хранит параметры инференса (`MAX_MODEL_LEN: 2048`, `BLOCK_SIZE: 32`, `KV_CACHE_MEMORY_BYTES: 512M`, `VLLM_ENABLE_V1_MULTIPROCESSING: 0`).
* **`Secret: vllm-secrets`:** безопасно хранит `hf-token` и `api-key`.
* **Kustomize (`kustomization.yaml`):** объединяет все манифесты, позволяя развертывать приложение одной командой `kubectl apply -k k8s/base`.

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

### 1. Клонирование репозитория
```bash
git clone https://github.com/dashkared/vllm-k8s-inference.git
cd vllm-k8s-inference
```

### 2. Предварительные требования
* Установленный **Docker Desktop** (с включенным Kubernetes: `Settings -> Kubernetes -> Enable Kubernetes`).
* Утилита **kubectl** (встроенная в Docker Desktop).

### 3. Сборка Docker-образов
Соберите легковесный initContainer и основной образ vLLM инференса:
```bash
docker build -t vllm-model-downloader:latest -f docker/Dockerfile.init docker/
docker build -t vllm-inference:latest -f docker/Dockerfile docker/
```

### 4. Развертывание в Kubernetes через Kustomize (Бонус 1)
```bash
kubectl apply -k k8s/base
```

Отслеживайте статус развертывания:
```bash
kubectl get pods,pvc,svc -l app=vllm-inference
```

#### Жизненный цикл запуска пода (важно для проверяющего!):
1. **Фаза 1: `Init:0/1` (~2–5 мин при первом старте, ~1 сек при повторных)**  
   Работает легковесный `initContainer` (`model-downloader`). Он скачивает веса модели `Qwen/Qwen2.5-1.5B-Instruct` (~3 ГБ) с Hugging Face на постоянный том `vllm-model-pvc` (`[CACHE MISS]`). При любых повторных перезапусках срабатывает `[CACHE HIT]`, и этот шаг завершается мгновенно.  
   *Логи скачивания весов:*
   ```bash
   kubectl logs -l app=vllm-inference -c model-downloader -f
   ```

2. **Фаза 2: `0/1 Running` или `PodInitializing` (~30–45 секунд)**  
   После завершения скачивания Kubernetes запускает основной контейнер `vllm-server`. Движок vLLM считывает 3 ГБ весов из диска PVC в оперативную память CPU, инициализирует KV-кэш и прогревает модель. **В этот период внутренний порт 8000 ещё закрыт и модель готовится к работе!**  
   *Логи инициализации сервера:*
   ```bash
   kubectl logs -l app=vllm-inference -c vllm-server -f
   ```

3. **Фаза 3: `1/1 Running` (Сервер полностью готов к инференсу)**  
   Сервер Uvicorn успешно стартовал (`Application startup complete`), Readiness Probe возвращает `200 OK`. Теперь под готов принимать запросы!

> [!IMPORTANT]
> **Не запускайте `port-forward` и запросы до перехода в статус `1/1 Running`!**  
> Если попытаться выполнить `kubectl port-forward` или отправить `curl`, пока статус пода остаётся `0/1 Running` (модель ещё загружается в RAM), ОС выдаст ошибку `connect: connection refused` либо `Empty reply from server`.  
> Переходите к шагу с `port-forward` только тогда, когда команда `kubectl get pods -l app=vllm-inference` выведет в колонке `READY` значение **`1/1`**.

> [!NOTE]
> **О модели и Hugging Face Token:**
> Выбранная модель `Qwen/Qwen2.5-1.5B-Instruct` является открытой (public), поэтому для её скачивания токен Hugging Face не требуется — загрузчик скачивает веса автоматически при первом старте. Если вы захотите протестировать gated/приватную модель (например, Meta-Llama-3), укажите ваш токен в файле `k8s/base/secret.yaml` в поле `hf-token`.

---

## Проверка безопасности и API Bearer Token

После того как под перешел в статус **`1/1 Running`**, откройте соединение с сервисом в отдельной вкладке терминала:
```bash
kubectl port-forward svc/vllm-service 8000:8000
```
*(Примечание: на серверах под Linux сервис также доступен напрямую через NodePort по адресу `http://localhost:30080`).*

### 1. Запрос БЕЗ токена (Ожидается отказ 401 Unauthorized):
```bash
curl -i http://localhost:8000/v1/models
```
*Ответ:* `HTTP/1.1 401 Unauthorized` (`{"error":"Unauthorized"}`).

### 2. Запрос С валидным Bearer Token (Успех 200 OK):
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-kaspersky-de-bearer-token-2026" \
  -d '{
    "model": "Qwen2.5-1.5B-Instruct",
    "messages": [{"role": "user", "content": "Привет, расскажи о себе в одном предложении."}],
    "max_tokens": 50
  }'
```

### 3. Проверка защиты приватности и аудита (Privacy Filter)
По требованиям ИБ (GDPR, 152-ФЗ) тексты пользовательских промптов и генераций не должны попадать в stdout/stderr контейнера, сохраняя при этом аудитные метаданные (timestamp, IP, endpoint, статус 200, tokens/sec).

* **Проверка логов боевого инференса:**
  Проверьте логи пода после выполнения запроса:
  ```bash
  kubectl logs -l app=vllm-inference -c vllm-server --tail=15
  ```
  *Результат:* В логах фиксируется только факт доступа (`POST /v1/chat/completions 200`) и метрики производительности (`Avg prompt throughput: X tokens/s`), а текст промпта и ответа **полностью скрыт**.

* **Тест маскирования модуля `PrivacyLogFilter`:**
  Выполните моментальный тест фильтрации прямо внутри контейнера:
  ```bash
  kubectl exec -it deployment/vllm-inference -c vllm-server -- python3 -c '
  import logging
  from privacy_filter import PrivacyLogFilter

  logger = logging.getLogger("audit")
  handler = logging.StreamHandler()
  handler.addFilter(PrivacyLogFilter())
  handler.setFormatter(logging.Formatter("[AUDIT_LOG] %(message)s"))
  logger.addHandler(handler)
  logger.setLevel(logging.INFO)

  logger.info("Request: {\"prompt\": \"SECRET_CARD_1234\", \"max_tokens\": 20}")
  logger.info("Response: {\"content\": \"CONFIDENTIAL_ANSWER_DATA\"}")
  '
  ```
  *Результат работы фильтра:*
  ```text
  [AUDIT_LOG] Request: {"prompt": "[REDACTED_PROMPT]", "max_tokens": 20}
  [AUDIT_LOG] Response: {"content": "[REDACTED_CONTENT]"}
  ```

---

## Нагрузочное тестирование и аналитика (Бонус 3)

Скрипт `benchmark/load_test.py` использует асинхронный стриминг (SSE) для вычисления точного **TTFT (Time To First Token)**, общей **Latency** и **Throughput (токенов/сек)**. Тестирование можно запускать двумя способами:

### Способ 1: Внутри кластера Kubernetes (Рекомендуемый, Cloud-Native Job)
Запуск теста прямо внутри кластера. Манифест автоматически развернет ConfigMap со скриптом и Kubernetes Job, который обратится напрямую к `http://vllm-service:8000` без необходимости проброса портов или установки сторонних библиотек на хост:
```bash
# 1. Запустить Job внутри кластера:
kubectl apply -f k8s/base/benchmark-job.yaml

# 2. Смотреть ход тестирования и итоговую таблицу метрик:
kubectl logs -l app=vllm-load-test -f
```

### Способ 2: С локального хоста (Client Mode)
Запуск скрипта тестирования со своего компьютера (требуется активный `kubectl port-forward svc/vllm-service 8000:8000`):
```bash
# Вариант А: через uv (быстро и изолированно, без загрязнения Python):
uv run --with aiohttp python3 benchmark/load_test.py --endpoint http://localhost:8000 --api-key sk-kaspersky-de-bearer-token-2026 --concurrency 2 --requests 2

# Вариант Б: через стандартный venv:
python3 -m venv .venv && source .venv/bin/activate && pip install aiohttp
python3 benchmark/load_test.py --endpoint http://localhost:8000 --api-key sk-kaspersky-de-bearer-token-2026 --concurrency 2 --requests 2
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
