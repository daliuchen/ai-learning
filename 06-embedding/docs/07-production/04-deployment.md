# 部署形态选型

> embedding 服务不一定要做成独立的 API 服务。按规模不同，有四种部署方式可选：直接放在应用进程里、TEI sidecar、独立微服务、全托管 API。这篇比较它们的优缺点和延迟，最后给一个 TEI 的完整部署示例。

---

## 1. 四种形态

| 形态 | 例子 | 适合 |
|------|------|------|
| App 进程内 | 直接 `sentence_transformers.encode` | 单机、低 QPS、追求简单 |
| Sidecar / 共享 | TEI / vLLM 同主机 | 多个服务共享 |
| 独立微服务 | Embedding API Service | 多业务调用、集中管理 |
| 全托管 API | OpenAI / Cohere / Voyage | 不想运维 |

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

这种方式部署最简单，没有网络开销，一份代码、一个 Dockerfile 就够了。问题在于多个服务很难复用，每个进程都要各自加载一份模型，升级模型时所有服务都得重启。

---

## 3. Text Embeddings Inference (TEI) Sidecar

TEI 是 HuggingFace 官方的推理服务，专门为 embedding 做了优化：

```bash
docker run -p 8080:80 --gpus all \
  -v ~/.cache/huggingface:/data \
  ghcr.io/huggingface/text-embeddings-inference:1.8 \
  --model-id BAAI/bge-large-zh-v1.5 \
  --max-batch-tokens 32768
```

应用通过 HTTP 调用：

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

TEI 自带 dynamic batching，能把 GPU 跑满；多个应用可以共享同一个 TEI，升级模型时也只需要换 TEI。代价只是多一跳网络，同主机大约 1ms，可以忽略。

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
  selector:
    matchLabels:
      app: embedding-service
  template:
    metadata:
      labels:
        app: embedding-service
    spec:
      containers:
      - name: tei
        image: ghcr.io/huggingface/text-embeddings-inference:1.8
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
import httpx

EMBED_URL = "http://embedding-service.default.svc.cluster.local/embed"

async def embed(texts):
    async with httpx.AsyncClient() as client:
        return (await client.post(EMBED_URL, json={"inputs": texts})).json()
```

独立服务可以让多个业务方共用，能单独扩缩容，模型升级对业务的影响也小。缺点是多一跳网络，并且要自己负责监控和容灾。

---

## 5. 全托管 API

```python
from openai import AsyncOpenAI


client = AsyncOpenAI()


async def embed(texts):
    resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]
```

托管 API 不用运维，和 SDK 集成最简单，模型也会自动更新。缺点有三个：量大时不划算；数据要传出公司，可能有合规问题；跨地区调用有延迟。

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

## 7. Latency 对比（embed 单条 100 字，示意量级）

| 形态 | Latency |
|------|---------|
| App 进程内（CPU）| 100-300ms |
| App 进程内（GPU）| 5-15ms |
| TEI sidecar（GPU）| 8-15ms |
| TEI 独立服务（同集群）| 10-20ms |
| OpenAI API | 150-400ms |
| Cohere / Voyage API | 100-300ms |

自部署在 GPU 上，延迟比调外部 API 低一个数量级以上，差距主要在网络往返上。

---

## 8. TEI 部署完整 demo

### docker-compose.yml

```yaml
services:
  tei:
    image: ghcr.io/huggingface/text-embeddings-inference:1.8
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
    image: ghcr.io/huggingface/text-embeddings-inference:1.8
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

没有 GPU 也能跑，前提是用小模型或量化模型：

```bash
docker run -p 8080:80 \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.8 \
  --model-id sentence-transformers/all-MiniLM-L6-v2 \
  --max-batch-tokens 16384
```

用 ONNX 或 INT8 量化还能再快一些。

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

上面按 CPU 利用率扩缩容只是最简单的写法。GPU 推理时 CPU 利用率不能反映真实负载，更合适的是按 QPS、请求排队时间这类自定义指标（Prometheus Adapter / KEDA）扩缩容。

---

## 11. Failover

```python
# 备用节点必须跑同一个模型、同一个版本：不同模型的向量不在一个空间里，
# 拿 OpenAI 的 query 向量去查 bge 建的索引，结果没有意义
PROVIDERS = [
    ("tei_primary", "http://embedding-service/embed"),
    ("tei_backup", "http://embedding-service.backup-cluster/embed"),   # 另一个集群 / 可用区
]


async def embed_with_failover(texts):
    for name, url in PROVIDERS:
        try:
            return await httpx_call(url, texts)
        except Exception as e:
            log.warning(f"{name} failed: {e}")
    raise RuntimeError("all embedding providers failed")
```

平时走主集群，挂了切到备用集群。备用节点只能是同一个模型：如果索引是用 OpenAI 的 text-embedding-3-small 建的，兜底也只能是它（比如换一个 region 或 Azure OpenAI 上的同名部署）；跨模型切换意味着整库重建索引，做不到"挂了临时切"。

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| 进程内部署，每次重启加载模型都很慢 | 做 warmup，让进程常驻 |
| TEI 升级后老模型不兼容 | 锁定 image tag |
| K8s 的 GPU 节点不够 | nodeSelector + tolerations |
| embedding 服务挂了没有兜底 | 准备一套跑同一模型的备用节点，不能临时切到别的模型 |

---

## 13. 下一步

- [监控：召回率 / Latency / Cost](./05-monitoring.md)
- [完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
- 和 LLM 服务一起部署：[OpenAI Agents SDK 手册的部署形态选型](../../../05-openai-agents-sdk/docs/07-production/01-deployment.md)
