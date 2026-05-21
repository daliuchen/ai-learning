# 部署形态选型

> **一句话**：embedding service 不一定是"独立 API 服务"——按规模选 **直接在应用进程 / TEI sidecar / 独立微服务 / 全托管 API** 四种形态，按需选。

---

## 1. 四种形态

| 形态 | 例子 | 适合 |
|------|------|------|
| **App 进程内** | 直接 `sentence_transformers.encode` | 单机 / 低 QPS / 简单 |
| **Sidecar / 共享** | TEI / vLLM 同主机 | 多服务共享 |
| **独立微服务** | Embedding API Service | 多业务调用、集中管理 |
| **全托管 API** | OpenAI / Cohere / Voyage | 不想运维 |

---

## 2. App 进程内

```python
# FastAPI app
from fastapi import FastAPI
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("BAAI/bge-large-zh-v1.5")
app = FastAPI()


@app.post("/embed")
def embed(text: str):
    vec = model.encode(text, normalize_embeddings=True).tolist()
    return {"vector": vec}
```

**优**：

- 部署最简单
- 无网络开销
- 一份代码 / Dockerfile

**劣**：

- 多服务复用难
- 模型加载在每个进程
- 升级模型要重启所有服务

---

## 3. Text Embeddings Inference (TEI) Sidecar

HuggingFace 官方推理服务，专为 embedding 优化：

```bash
docker run -p 8080:80 --gpus all \
  -v ~/.cache/huggingface:/data \
  ghcr.io/huggingface/text-embeddings-inference:1.5 \
  --model-id BAAI/bge-large-zh-v1.5 \
  --max-batch-tokens 32768
```

App 通过 HTTP 调：

```python
import httpx


async def embed(texts: list[str]):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8080/embed",
            json={"inputs": texts, "normalize": True},
        )
        return resp.json()
```

**优**：

- dynamic batching 自动批
- GPU 跑满
- 多 app 共享同一 TEI
- 升级模型只换 TEI

**劣**：

- 网络一跳（同主机 ~1ms 可忽略）

---

## 4. 独立微服务

K8s deployment：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: embedding-service
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: tei
        image: ghcr.io/huggingface/text-embeddings-inference:1.5
        args:
        - --model-id=BAAI/bge-large-zh-v1.5
        - --max-batch-tokens=65536
        resources:
          requests:
            nvidia.com/gpu: 1
            memory: 8Gi
          limits:
            nvidia.com/gpu: 1
            memory: 16Gi
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: embedding-service
spec:
  selector:
    app: embedding-service
  ports:
  - port: 80
```

```python
# 应用调
EMBED_URL = "http://embedding-service.default.svc.cluster.local/embed"

async def embed(texts):
    return (await httpx.post(EMBED_URL, json={"inputs": texts})).json()
```

**优**：

- 多业务方共用
- 独立扩缩容
- 模型升级影响小

**劣**：

- 多一跳网络
- 要监控 / 容灾

---

## 5. 全托管 API

```python
from openai import AsyncOpenAI


client = AsyncOpenAI()


async def embed(texts):
    resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]
```

**优**：

- 0 运维
- 跟 SDK 集成最简
- 模型自动更新

**劣**：

- 贵（量大不划算）
- 数据出公司（合规问题）
- 跨地区延迟

---

## 6. 决策树

```
合规要求数据不出公司？
├─ 是 → 自部署（TEI / 独立服务）
└─ 否 ↓

量级？
├─ < 100 万 doc + 低 QPS → 全托管 API
├─ 100 万-1000 万 → TEI sidecar 或 独立服务
└─ > 1000 万 → 独立服务集群

团队能运维 GPU？
├─ 不能 → 全托管
└─ 能 → 自部署省钱
```

---

## 7. Latency 对比（embed 单条 100 字）

| 形态 | Latency |
|------|---------|
| App 进程内（CPU）| 100-300ms |
| App 进程内（GPU）| 5-15ms |
| TEI sidecar（GPU）| 8-15ms |
| TEI 独立服务（同集群）| 10-20ms |
| OpenAI API | 150-400ms |
| Cohere / Voyage API | 100-300ms |

自部署 GPU 比 API 快 10-30 倍。

---

## 8. TEI 部署完整 demo

### docker-compose.yml

```yaml
services:
  tei:
    image: ghcr.io/huggingface/text-embeddings-inference:1.5
    ports:
      - "8080:80"
    volumes:
      - ./hf_cache:/data
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    command:
      - --model-id=BAAI/bge-large-zh-v1.5
      - --max-batch-tokens=32768
      - --pooling=cls
  
  reranker:
    image: ghcr.io/huggingface/text-embeddings-inference:1.5
    ports:
      - "8081:80"
    volumes:
      - ./hf_cache:/data
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    command:
      - --model-id=BAAI/bge-reranker-large
      - --max-batch-tokens=32768
```

```bash
docker compose up -d
```

### Python client

```python
import httpx


async def embed(texts):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8080/embed",
            json={"inputs": texts, "normalize": True},
        )
        return resp.json()


async def rerank(query, docs):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8081/rerank",
            json={"query": query, "texts": docs},
        )
        return resp.json()
```

---

## 9. CPU 部署

没 GPU 也行（小模型 / 量化）：

```bash
docker run -p 8080:80 \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.5 \
  --model-id sentence-transformers/all-MiniLM-L6-v2 \
  --max-batch-tokens 16384
```

ONNX / INT8 量化能进一步提速。

---

## 10. 高可用 / 扩容

```yaml
# K8s HPA
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: embedding-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: embedding-service
  minReplicas: 3
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

---

## 11. Failover

```python
PROVIDERS = [
    ("local_tei", "http://embedding-service/embed"),
    ("openai", openai_embed),
]


async def embed_with_failover(texts):
    for name, provider in PROVIDERS:
        try:
            if name == "openai":
                return await provider(texts)
            else:
                return await httpx_call(provider, texts)
        except Exception as e:
            log.warn(f"{name} failed: {e}")
    raise RuntimeError("all embedding providers failed")
```

主用自部署，挂了切 OpenAI。

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| App 进程内每次重启加载模型慢 | warmup + 进程常驻 |
| TEI 跑老 model 不兼容新版 | 锁定 image tag |
| K8s GPU node 不够 | nodeSelector + tolerations |
| OpenAI 限流没 fallback | failover 链路 |

---

## 13. 下一步

- 📖 监控 → [05-monitoring.md](./05-monitoring.md)
- 📖 完整 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
- 📖 跟 LLM 服务一起部署 → [05-openai-agents-sdk/07-production/01-deployment.md](../../../05-openai-agents-sdk/docs/07-production/01-deployment.md)
