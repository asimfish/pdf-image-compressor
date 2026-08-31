# 部署教程

[English](DEPLOYMENT.md) | 中文

四种把 PaperSqueeze 部署成匿名、无状态公开压缩站的方式。它们使用同一个
容器镜像和同一套[配置项](../README_CN.md#9--配置项)。

- [Docker（任意平台）](#docker任意平台)
- [Modal——免费额度](#modal免费额度)
- [Hugging Face Spaces](#hugging-face-spaces)
- [Google Cloud Run](#google-cloud-run)

---

## Docker（任意平台）

直接运行已发布镜像（每次合入 `main` 后由 CI 构建、冒烟测试并推送；
正式版本另有 `vX.Y.Z` / `X.Y.Z` 标签）：

```bash
docker pull ghcr.io/asimfish/pdf-image-compressor:latest
docker run --rm -p 8080:8080 \
  -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  -e PDF_COMPRESSOR_MAX_UPLOAD_MB=30 \
  ghcr.io/asimfish/pdf-image-compressor:latest
```

或本地构建：

```bash
docker build -t papersqueeze .
docker run --rm -p 8080:8080 -e PDF_COMPRESSOR_PUBLIC_MODE=1 papersqueeze
```

访问 <http://127.0.0.1:8080>，健康检查为 `GET /api/health`。
镜像是平台中立的 OCI 格式，可部署到任何容器平台。

如需给公开实例加访问密码，追加
`-e PDF_COMPRESSOR_ACCESS_PASSWORD=你的密码`。

## Modal——免费额度

Modal Starter 当前每月提供 $30 计算额度，容器空闲时自动缩容到 0，
适合低频公开访问。

```bash
# 1. 首次使用：创建账号并登录
uvx modal setup

# 2. 在仓库根目录直接部署
uvx modal deploy deploy_modal.py
```

脚本使用 2 CPU、2 GiB 内存，最多 1 个容器；应用内部同时只允许 1 个
压缩任务。空闲 60 秒后缩容，不保留上传文件。部署成功后 Modal 会输出
稳定的 `modal.run` HTTPS 地址。

```bash
uvx modal billing                 # 查看用量
uvx modal app stop papersqueeze   # 停止应用
```

免费额度和平台政策可能变化；如账户绑定了付费方式，请在 Modal 控制台
设置预算提醒。冷启动以及超过 150 秒的请求可能出现跳转或延迟。

## Hugging Face Spaces

Docker Spaces 当前即使使用 `cpu-basic` 也需要 PRO 订阅；免费账号只能
创建无法运行本项目 Python 后端的 Static Space。

```bash
# 1. 登录
uvx --from huggingface_hub hf auth login

# 2. 部署（默认创建公开的 <用户名>/papersqueeze）
uv run --no-project deploy_huggingface_space.py

# 自定义组织或名称
HF_SPACE_ID=your-org/your-space uv run --no-project deploy_huggingface_space.py
```

Space 使用仓库现有 Dockerfile，监听 8080 端口，默认启用公开无状态模式。
脚本按完整快照覆盖部署，Space 中手动添加的其他文件会被清除。实例可能
休眠，首次访问需要等待冷启动。

## Google Cloud Run

仓库附带可重复执行的部署脚本。默认：东京区域、1 GiB 内存、并发 1、
最大 1 实例、空闲缩容到 0。

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
bash deploy_cloud_run.sh

# 覆盖默认值
CLOUD_RUN_REGION=asia-east1 CLOUD_RUN_SERVICE=my-pdf-compressor bash deploy_cloud_run.sh
```

说明：

- Cloud Run 的 HTTP/1 请求上限为 32 MiB，公开模式默认 30 MiB 的上传上限
  正好放得下。
- 1 GiB 内存是根据真实样本约 689 MiB 峰值设置的安全下限。
- `--max-instances 1` 和 `--concurrency 1` 用于限制匿名公开服务的资源
  消耗；请同时在 Google Cloud 设置预算告警。
- 脚本会启用 Cloud Run、Cloud Build 和 Artifact Registry API；首次构建
  可能产生少量费用。
- 匿名模式没有用户级配额；若面向大量公众长期运营，请在前方增加
  Cloud Armor、API Gateway 或其他限流/鉴权层——或直接设置
  `PDF_COMPRESSOR_ACCESS_PASSWORD`。
