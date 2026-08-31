<div align="center">

# PaperSqueeze 📄🗜️

[![CI](https://github.com/asimfish/pdf-image-compressor/actions/workflows/ci.yml/badge.svg)](https://github.com/asimfish/pdf-image-compressor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/asimfish/pdf-image-compressor)](https://github.com/asimfish/pdf-image-compressor/releases)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Live Demo](https://img.shields.io/badge/live-demo-126b4f.svg)](https://liyufeng854--papersqueeze-serve.modal.run)

[English](README.md) | **中文**

> 🎯 **把论文 PDF 压到任意目标大小——但不牺牲文字。**
> PaperSqueeze 只重新编码真正占空间的图片，完整保留可搜索文字、公式、
> 超链接、矢量图形和透明图层。

[**在线演示 →**](https://liyufeng854--papersqueeze-serve.modal.run)（免费实例，空闲时缩容，首次访问可能需要等待冷启动）

</div>

---

## 目录

1. [为什么选 PaperSqueeze](#1-为什么选-papersqueeze)
2. [快速开始](#2--快速开始)
3. [主要能力](#3--主要能力)
4. [实测结果](#4--实测结果)
5. [压缩模式](#5--压缩模式)
6. [CLI 使用](#6--cli-使用)
7. [Web UI](#7--web-ui)
8. [部署](#8--部署)
9. [配置项](#9--配置项)
10. [API](#10--api)
11. [测试与开发](#11--测试与开发)
12. [贡献](#12--贡献)
13. [引用](#13--引用)
14. [Star History](#14--star-history)
15. [许可证](#15-许可证)

---

## 1. 为什么选 PaperSqueeze

大多数 PDF 压缩器为了达到目标大小会把整页栅格化——论文变成一叠截图：
不能选中文字、不能搜索、链接失效、公式发糊。投稿系统不在乎，读者在乎。

PaperSqueeze 反其道而行：

- **只重编码图片。** 文字、字体、矢量图和链接注释原样保留。
- **透明度不丢失。** 重编码后恢复 PDF 外置软遮罩（`SMask`），
  半透明标注不会变成黑底或白框。
- **绝不偷偷栅格化。** 默认 `fidelity` 模式与 `auto` 模式都拒绝栅格化；
  保留文字层达不到目标时，返回最接近的保真结果——只有显式选择
  `raster` 模式才会牺牲文字层。

## 2. 🚀 快速开始

需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装
git clone https://github.com/asimfish/pdf-image-compressor.git
cd pdf-image-compressor
uv sync --locked --extra dev

# 2. 压缩到目标大小（默认 fidelity 模式，绝不栅格化）
uv run file-compressor compress paper.pdf --target-size 3MB

# 3. 或者用浏览器界面
uv run file-compressor web        # → http://127.0.0.1:8765
```

不想安装？直接用[在线演示](https://liyufeng854--papersqueeze-serve.modal.run)，
或运行已发布的容器：

```bash
docker run --rm -p 8080:8080 -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  ghcr.io/asimfish/pdf-image-compressor:latest   # → http://127.0.0.1:8080
```

## 3. ✨ 主要能力

- 🎯 **目标大小压缩**——输入 `500KB`、`3MB` 或纯字节数，自动寻找
  不超过上限的最高质量结果。
- 🔤 **文字优先**——除显式 `raster` 模式外，所有模式都保留可搜索
  文字、复制和超链接。
- 🪟 **透明图层保真**——重编码后恢复外置软遮罩（含半透明、共享遮罩、
  间接类型引用和 `Matte`）。
- 🧭 **诚实的自动模式**——按「无损优化 → 保留文字重压图片」执行；
  达不到目标时返回最接近的保真结果，而不是偷偷栅格化。
- 🎚️ **四档压缩强度**——1 近无损、2 均衡、3 激进、4 最大压缩。
- 🖥️ **CLI + Web UI**——单文件、目录批量、JSON 报告或浏览器操作；
  也支持独立图片（JPEG/PNG/WebP）。
- 🌐 **两种网站模式**——本地持久化资料库，或匿名无状态公开站点
  （自动清理临时文件）。
- 🔐 **可选访问门禁**——用 `PDF_COMPRESSOR_ACCESS_PASSWORD` 给公开部署
  加访问密码；浏览器验证一次 30 天内免输入。
- 📦 **可验证容器**——每次推送 `main`，CI 构建、冒烟测试并发布到
  GitHub Container Registry；正式版本带版本化标签。

## 4. 📊 实测结果

已部署的演示站使用默认 `3MB` 目标，在线处理一份 20 页、30,628,839 字节的
论文 PDF，并与 3,194,322 字节的参考文件比较：

| 指标 | 结果 |
| --- | --- |
| 输出大小 | **2,773,250 字节**（比参考文件再小 421,072 字节） |
| 压缩率 | **90.95%** |
| 文字 | 80,827 个字符，和原件完全一致 |
| 链接 | 163 个，和原件完全一致 |
| 透明度 | **65 处 SMask 引用全部保留** |
| 视觉质量 | 20 页 RGB 多通道 SSIM 平均 **0.9993**，最低 0.9961 |
| 端到端耗时 | 线上约 62 秒（含上传、处理、下载）；本机 61–84 秒 |

可用自包含基准脚本复现（依赖由 `uv` 临时安装，不进入生产镜像）：

```bash
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf \
  --reference reference.pdf --dpi 96

# 机器可读报告
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf --json
```

比较要求页数和每页尺寸完全一致，不会通过缩放或裁剪掩盖几何变化。
测试论文不包含在仓库中；评估采用「同一原件、同一字节上限」的方式，
而不是对不同内容做误导性的逐像素比较。

## 5. 🧭 压缩模式

| 模式 | 文字层 | 行为 |
| --- | --- | --- |
| `fidelity`（默认） | ✅ 保留 | 质量优先：先无损优化，再温和重压图片；能达到目标时优先无损候选 |
| `auto` | ✅ 保留 | 比 `fidelity` 更强的目标搜索；仍不栅格化，达不到目标返回最接近的保真结果 |
| `text` | ✅ 保留 | 保留文字前提下最大力度重压图片 |
| `optimize` | ✅ 保留 | 仅做无损结构优化 |
| `raster` | ❌ 丢失 | 按 `--pdf-dpi` 整页栅格化——唯一牺牲文字的模式，永不被隐式选择 |

## 6. ⌨️ CLI 使用

```bash
# 默认：保真优先，不栅格化，尽量压到 3 MB 以内
uv run file-compressor compress input.pdf --target-size 3MB

# 更强的目标搜索，仍然保留文字
uv run file-compressor compress input.pdf --target-size 3MB --pdf-mode auto

# 保留文字层，允许更强地重压图片
uv run file-compressor compress input.pdf --target-size 3MB --pdf-mode text

# 极限压缩——会失去文字层（必须显式选择）
uv run file-compressor compress input.pdf --target-size 800KB \
  --pdf-mode raster --pdf-dpi 120

# 批量处理目录并输出 JSON 报告
uv run file-compressor compress ./papers --output-dir ./compressed --json-report
```

`--target-size` 支持 `500KB`、`2MB`、`1.5GB` 或纯字节数。
其他常用参数：`--compression-level 1..4`、`--pdf-grayscale`、
`--keep-metadata`、`--to-webp`（图片）、`--archive zip`。

## 7. 🖥️ Web UI

**本地资料库模式**（持久化）：

```bash
uv run file-compressor web                          # → http://127.0.0.1:8765
uv run file-compressor web --data-dir /path/to/lib  # 自定义资料库目录
```

PDF、备注和压缩版本保存在 `~/.pdf-manager`。该模式没有用户鉴权，
请只监听本机或可信内网。

**公开无状态模式**（匿名）：只开放首页、配置、健康检查和单文件压缩接口；
每个上传在独立临时目录中处理，响应完成后自动删除。默认启用严格安全响应头，
并禁用 OpenAPI 文档。

```bash
PDF_COMPRESSOR_PUBLIC_MODE=1 uv run file-compressor web --host 0.0.0.0 --port 8080
```

限额、超时、限流和可选密码门禁见[配置项](#9--配置项)；仓库附带注释完整的
[`.env.example`](.env.example)。

## 8. ☁️ 部署

```bash
docker run --rm -p 8080:8080 -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  ghcr.io/asimfish/pdf-image-compressor:latest
```

**Docker / Modal（免费额度）/ Hugging Face Spaces / Google Cloud Run**
的分步教程见 [docs/DEPLOYMENT_CN.md](docs/DEPLOYMENT_CN.md)
（[English](docs/DEPLOYMENT.md)）。镜像是平台中立的 OCI 格式，
任何支持容器的平台都能运行。

## 9. ⚙️ 配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PDF_COMPRESSOR_PUBLIC_MODE` | 关 | 设为 `1` 启用匿名无状态公开站点 |
| `PDF_COMPRESSOR_ACCESS_PASSWORD` | 未设置 | 公开模式可选访问密码；浏览器验证一次 30 天内免输入 |
| `PDF_COMPRESSOR_MAX_UPLOAD_MB` | 公开 30 / 资料库 500 | 单文件上限（1–500 MiB） |
| `PDF_COMPRESSOR_MAX_PAGES` | 100 | 公开模式最大页数（1–2000） |
| `PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS` | 120 | 公开模式上传总时限（10–900 秒） |
| `PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS` | 300 | 公开模式压缩时限（30–1800 秒）；超时终止隔离进程 |
| `PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS` | 120 | 公开模式下载时限（10–900 秒）；超时清理临时文件 |
| `PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE` | 12 | 单实例每分钟接受的压缩请求数（1–120） |
| `PDF_COMPRESSOR_CONCURRENCY` | 1 | 单实例并发压缩任务数（1–4） |
| `PORT` | 8080 | 容器监听端口 |

## 10. 🔌 API

公开模式：

- `GET /`——公开压缩页面
- `GET /api/health`——健康检查
- `GET /api/config`——前端上传限制
- `POST /compress`——上传并返回压缩后的单个 PDF

本地资料库模式还提供 `/api/pdfs`、`/api/versions`、批量压缩、批量删除、
页面预览、备注和版本下载接口。开发模式可在 `/docs` 查看完整 OpenAPI 文档。

## 11. 🧪 测试与开发

```bash
uv sync --locked --extra dev
uv run ruff check src tests scripts deploy_huggingface_space.py deploy_modal.py
uv run pip-audit --local --skip-editable
uv run python -m pytest -q
uv build
```

当前测试套件包含 **414 个用例**，覆盖 CLI、PDF/图片压缩、目标大小、
文字与透明图层保留（含半透明、共享软遮罩、间接类型引用和 `Matte`）、
视觉质量基准、Web API、部署打包、存储和错误处理。GitHub Actions 在
Python 3.10 与 3.12 上运行 Ruff、依赖漏洞审计和测试，并构建分发包、
验证容器。

## 12. 🤝 贡献

欢迎提交 Issue 和 Pull Request——开发环境与提交要求见
[CONTRIBUTING.md](CONTRIBUTING.md)。涉及压缩行为的改动，请报告文件大小、
文字/链接/软遮罩保留情况、至少一项视觉指标（SSIM/PSNR）、运行时间与
峰值内存，并附回归测试。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 13. 📖 引用

如果 PaperSqueeze 对你的工作有帮助，欢迎引用（见 [CITATION.cff](CITATION.cff)）：

```bibtex
@software{papersqueeze,
  author  = {Li, Yufeng},
  title   = {PaperSqueeze: target-aware PDF compression that preserves searchable text, links, and vector content},
  year    = {2026},
  url     = {https://github.com/asimfish/pdf-image-compressor}
}
```

## 14. ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=asimfish/pdf-image-compressor&type=Date)](https://star-history.com/#asimfish/pdf-image-compressor&Date)

## 15. 许可证

[MIT](LICENSE)
