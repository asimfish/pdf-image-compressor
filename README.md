# PaperSqueeze

[![CI](https://github.com/asimfish/pdf-image-compressor/actions/workflows/ci.yml/badge.svg)](https://github.com/asimfish/pdf-image-compressor/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

面向论文和技术文档的开源 PDF 压缩器。它优先保留可搜索文字、公式、链接和矢量结构，只重新编码真正占空间的图片；只有在目标大小无法通过保留文字的方式达到时，`auto` 模式才会整页栅格化。

既可以作为本地 PDF 版本管理器使用，也可以部署为匿名、无状态的公开压缩网站。

## 主要能力

- **目标大小压缩**：输入 `500KB`、`3MB` 等目标，自动寻找不超过上限的高质量结果。
- **文字优先**：`text` 模式永不栅格化页面，保留复制、搜索和超链接。
- **透明图层保真**：重新编码图片后恢复 PDF 外置软遮罩（`SMask`），避免透明标注变成黑底或白框。
- **质量优先的自动模式**：按“无损优化 → 保留文字重压图片 → 栅格化兜底”的顺序执行。
- **四档压缩强度**：1 近无损、2 均衡、3 激进、4 最大压缩。
- **CLI 与 Web UI**：支持单文件、目录批量、JSON 报告和浏览器操作。
- **两种网站模式**：本地持久化资料库；公开部署时使用匿名无状态页面并自动清理临时文件。

## 实测结果

本地使用一份 20 页、30,628,839 字节的论文 PDF，以 3,194,322 字节为目标上限：

- 输出大小：**2,773,250 字节**，比目标参考文件再小 421,072 字节。
- 压缩率：**90.95%**。
- 文字：80,827 个字符，和原件完全一致。
- 链接：163 个，和原件完全一致。
- 透明度：原件中的 **65 处 SMask 引用全部保留**，透明文字和标注不会被烘焙成白框。
- 视觉质量：20 页相对原件的 RGB 多通道 SSIM 平均为 **0.9993**，最低 0.9961。
- 性能：本机多次运行约 61–84 秒；具体时间取决于 CPU 负载、PDF 图像数量和目标大小。

参考 PDF 与原件不是完全相同的论文版本，因此项目采用“同一原件、同一字节上限”的方式评估压缩质量，而不是对不同内容做误导性的逐像素比较。测试论文文件不包含在仓库中。

可用自包含基准脚本复现同样的文件大小、文字、链接、透明软遮罩和逐页 RGB 多通道 SSIM 检查；依赖由 `uv` 临时安装，不会进入生产镜像：

```bash
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf \
  --reference reference.pdf \
  --dpi 96

# 机器可读报告
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf --json
```

比较要求页数和每页尺寸完全一致，不会通过缩放或裁剪掩盖几何变化。

## 安装

需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/asimfish/pdf-image-compressor.git
cd pdf-image-compressor
uv sync --extra dev
```

## CLI 使用

```bash
# 智能压缩到 3 MB
uv run file-compressor compress input.pdf \
  --target-size 3MB \
  --pdf-mode auto \
  --compression-level 2

# 始终保留文字层
uv run file-compressor compress input.pdf \
  --target-size 3MB \
  --pdf-mode text

# 极限压缩；会失去文字层
uv run file-compressor compress input.pdf \
  --target-size 800KB \
  --pdf-mode raster \
  --pdf-dpi 120

# 批量处理目录并输出 JSON 报告
uv run file-compressor compress ./papers \
  --output-dir ./compressed \
  --json-report
```

`--target-size` 支持 `500KB`、`2MB`、`1.5GB` 或纯字节数。

## 本地 Web UI

```bash
uv run file-compressor web
```

打开 <http://127.0.0.1:8765>。默认模式会在 `~/.pdf-manager` 保存 PDF、备注和不同压缩版本。该资料库模式没有用户鉴权，请只监听本机或可信内网。可指定其他目录：

```bash
uv run file-compressor web --data-dir /path/to/library
```

## 公开无状态模式

公开模式只开放首页、配置、健康检查和单文件压缩接口，不暴露本地资料库 API：

```bash
PDF_COMPRESSOR_PUBLIC_MODE=1 \
PDF_COMPRESSOR_MAX_UPLOAD_MB=30 \
PDF_COMPRESSOR_MAX_PAGES=100 \
PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS=120 \
PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS=300 \
PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS=120 \
PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE=12 \
PDF_COMPRESSOR_CONCURRENCY=1 \
uv run file-compressor web --host 0.0.0.0 --port 8080
```

文件在独立临时目录中处理，响应完成后自动删除。公开服务默认禁用 OpenAPI 文档，添加 CSP、`nosniff`、禁止嵌入等安全响应头，并限制为单文件、单压缩任务。公开模式未显式配置上传上限时默认为 30 MiB。

## Docker

```bash
docker build -t papersqueeze .
docker run --rm -p 8080:8080 \
  -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  -e PDF_COMPRESSOR_MAX_UPLOAD_MB=30 \
  papersqueeze
```

访问 <http://127.0.0.1:8080>，健康检查为 `GET /api/health`。

每次推送到 `main` 后，CI 会构建、启动并冒烟测试容器，再发布到 GitHub Container Registry：

```bash
docker pull ghcr.io/asimfish/pdf-image-compressor:latest
docker run --rm -p 8080:8080 \
  ghcr.io/asimfish/pdf-image-compressor:latest
```

这份镜像可部署到任何支持 Docker/OCI 的平台，不依赖特定云厂商。

## 免费部署到 Modal

Modal Starter 当前每月提供 $30 免费计算额度，容器空闲时自动缩容到 0，适合低频公开访问。首次使用先创建账号并登录：

```bash
uvx modal setup
```

登录完成后直接部署当前工作区：

```bash
uvx modal deploy deploy_modal.py
```

脚本使用 2 CPU、2 GiB 内存和 Modal 默认临时磁盘，最多只启动 1 个容器；容器可同时响应 8 个轻量 HTTP 请求，但应用内部仍只允许 1 个压缩任务。空闲 60 秒后缩容，不保留上传文件。部署成功后 Modal 会输出稳定的 `modal.run` HTTPS 地址。查看用量或停止应用：

```bash
uvx modal billing
uvx modal app stop papersqueeze
```

免费额度和平台政策可能变化；如账户绑定了付费方式，请在 Modal 控制台设置预算提醒。冷启动以及超过 150 秒的请求可能发生跳转或延迟。

## 部署到 Hugging Face Spaces

Hugging Face Docker Spaces 不要求 Google Cloud 结算账号，但当前即使使用 `cpu-basic` 也需要 Hugging Face PRO；免费账号只能创建无法运行本项目 Python 后端的 Static Space。订阅 PRO 后，先登录，再运行仓库内的部署脚本：

```bash
uvx --from huggingface_hub hf auth login
uv run --no-project deploy_huggingface_space.py
```

脚本默认创建公开的 `<HF用户名>/papersqueeze`。如需使用组织或其他名称：

```bash
HF_SPACE_ID=your-org/your-space \
uv run --no-project deploy_huggingface_space.py
```

Space 使用现有 Dockerfile，监听 8080 端口，并默认启用公开无状态模式。脚本按完整快照覆盖部署，Space 中手动添加的其他文件会被清除。实例可能休眠，首次访问需要等待冷启动。

## 部署到 Google Cloud Run

项目附带可重复执行的部署脚本，默认使用东京区域、1 GiB 内存、并发 1、最大实例 1、空闲缩容到 0：

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
bash deploy_cloud_run.sh
```

也可以覆盖默认值：

```bash
CLOUD_RUN_REGION=asia-east1 \
CLOUD_RUN_SERVICE=my-pdf-compressor \
bash deploy_cloud_run.sh
```

说明：

- Cloud Run 的 HTTP/1 请求上限为 32 MiB，因此公开部署默认限制为 30 MiB；示例中的 29.2 MiB 论文可以上传。
- 1 GiB 是根据真实样本约 689 MiB 峰值设置的安全下限。
- `--max-instances 1` 和 `--concurrency 1` 用于限制公开匿名服务的资源消耗；请同时在 Google Cloud 设置预算告警。
- 脚本会启用 Cloud Run、Cloud Build 和 Artifact Registry API；首次构建可能产生少量云资源费用。
- 匿名模式没有用户级配额；若面向大量公众长期运营，请在前方增加 Cloud Armor、API Gateway 或其他限流/鉴权层。

## 配置项

- `PDF_COMPRESSOR_PUBLIC_MODE`：设为 `1` 启用公开无状态页面。
- `PDF_COMPRESSOR_MAX_UPLOAD_MB`：单文件上限，范围 1–500 MiB；公开模式默认 30，资料库模式默认 500。
- `PDF_COMPRESSOR_MAX_PAGES`：公开 PDF 最大页数，范围 1–2000。
- `PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS`：公开模式上传总时限，范围 10–900 秒，默认 120。
- `PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS`：公开模式压缩进程总时限，范围 30–1800 秒，默认 300；超时会终止隔离进程。
- `PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS`：公开模式下载总时限，范围 10–900 秒，默认 120；超时会终止传输并清理临时文件。
- `PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE`：单实例每分钟最多接受的压缩请求数，范围 1–120，默认 12。
- `PDF_COMPRESSOR_CONCURRENCY`：单实例同时接收、压缩和下载的任务数，范围 1–4。
- `PORT`：容器监听端口，默认 `8080`。

## API

公开模式：

- `GET /`：公开压缩页面。
- `GET /api/health`：健康检查。
- `GET /api/config`：前端上传限制。
- `POST /compress`：上传并返回压缩后的单个 PDF。

本地资料库模式还提供 `/api/pdfs`、`/api/versions`、批量压缩、批量删除、页面预览、备注和版本下载接口。开发模式可在 `/docs` 查看完整 OpenAPI 文档。

## 测试

```bash
uv sync --locked --extra dev
uv run ruff check src tests scripts deploy_huggingface_space.py deploy_modal.py
uv run pip-audit --local --skip-editable
uv run python -m pytest -q
uv build
```

当前测试套件包含 407 个用例，覆盖 CLI、PDF/image 压缩、目标大小、文字与透明图层保留（含半透明、共享软遮罩、间接类型引用和 `Matte`）、视觉质量基准、Web API、部署打包、存储和错误处理。GitHub Actions 会在 Python 3.10 与 3.12 上运行 Ruff、依赖漏洞审计和测试，并构建分发包、验证容器。

## 开源与贡献

欢迎提交 Issue 和 Pull Request。涉及压缩算法的改动，请同时提供：

1. 原始大小与输出大小。
2. 文本和链接的数量及语义指纹是否保留。
3. 至少一项视觉质量指标（如 SSIM/PSNR）。
4. 运行时间与峰值内存。
5. 对应的回归测试。

本项目使用 [MIT License](LICENSE)。
