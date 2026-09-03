# 基准对比：PaperSqueeze vs PixShift

[English](BENCHMARK.md) | 中文

本文记录 README 中引用数字背后的正面对比，保证每个结论都可核查、可复跑。

## 环境

| 项目 | 取值 |
| --- | --- |
| PaperSqueeze | 提交 `3a30a9c`（main，2026-09-03），`--pdf-mode auto` |
| PixShift | 2.0.0，通过 `uvx pixshift pdf compress --target-size <bytes>B` 调用 |
| 机器 | Apple 芯片 MacBook，12 线程，macOS 15 |
| 运行时 | Python 3.13、PyMuPDF 1.28、Pillow 11 |
| 预算 | 两边字节数完全相同（MiB 预算，以纯字节数传入） |
| 评分 | `scripts/compare_pdf_quality.py`：96 DPI 逐页 RGB 多通道 SSIM，文字 / 链接 / 软遮罩 SHA-256 指纹，页数与页面几何 |
| 计时 | CLI 进程墙钟时间，单次运行，机器空闲 |

两个工具都由 `scripts/benchmark_corpus.py` 驱动。PixShift 使用默认设置；它在目标模式下从不降采样，并且不会处理带软遮罩、`/Mask`、模板标志或非默认 `/Decode` 的图像。

## 语料

语料不随仓库分发（出版社版权论文、私人笔记导出、会议手册）。下表足以组装一套等价语料。

| 名称 | 类型 | 大小 | 页数 | 图片 | 特点 | 预算 |
| --- | --- | --- | --- | --- | --- | --- |
| Study Notes | iPad 手写笔记导出 | 27.5 MB | 74 | 76 | 全部图片带 Display P3 ICC | 10 MiB |
| CycleGAN | arXiv 1703.10593 | 37.6 MB | 18 | 650 | 308 个软遮罩，JPX 图像，330 个链接 | 8 MiB |
| arXiv 2211 | arXiv 2211.17091v4 | 28.9 MB | 48 | 160 | 48 个软遮罩，431 个链接，矢量场插图 | 6 MiB |
| Nature | Nature 论文 PDF | 10.4 MB | 12 | 29 | 4250 px 照片，小尺寸图表 PNG | 3 MiB |
| 会议手册 | 会议日程（InDesign） | 26.6 MB | 42 | 181 | 162 张 DeviceCMYK JPEG，文字已转矢量轮廓（13.3 MB） | 8 MiB |
| 教科书 | 统计学教材扫描件 | 18.4 MB | 251 | 251 | 16 色索引页面扫描 | 8 MiB |
| 幻灯片 | 答辩幻灯片 | 5.6 MB | 41 | 165 | 108 个软遮罩，1.4 MB 字体/矢量 | 2 MiB |
| CVPR | CVPR 2024 论文（NeRF） | 10.0 MB | 11 | 12 | 带软遮罩的大尺寸 PNG 渲染图 | 3 MiB |

## 结果

| 文档 | PaperSqueeze | PixShift |
| --- | --- | --- |
| Study Notes | 10,482,389 B（100.0%）· SSIM 0.9910 / 0.9699 · **5 s** | 10,283,650 B（98.1%）· SSIM 0.9915 / 0.9724 · 52 s |
| CycleGAN | 8,310,231 B（99.1%）· SSIM **0.9985** / 0.9928 · **8 s** | 8,379,020 B（99.9%）· SSIM 0.9941 / 0.9819 · 270 s |
| arXiv 2211 | 6,273,262 B（99.7%）· SSIM 0.9775 / 0.8920 · **15 s** | **失败**（`target_size_unreachable`）· 208 s |
| Nature | 3,130,022 B（99.5%）· SSIM 0.9998 / 0.9987 · **3 s** | 3,144,949 B（100.0%）· SSIM 0.9998 / 0.9987 · 33 s |
| 会议手册 | 13,529,697 B（161%，超出）· SSIM 0.9979 / 0.9804 · 39 s | **失败** · 1468 s |
| 教科书 | 8,325,624 B（99.2%）· SSIM 0.9813 / 0.8667 · **34 s** | **失败** · 1144 s |
| 幻灯片 | 2,859,992 B（136%，超出）· SSIM 0.9821 / 0.9110 · 5 s | **失败** · 101 s |
| CVPR | 3,094,726 B（98.4%）· SSIM 0.9997 / 0.9985 · **3 s** | **失败** · 82 s |

SSIM 为 均值 / 最差页。百分比为输出大小相对预算的比例。

**预算达标：6/8 vs 3/8。总墙钟时间：111 s vs 3,356 s（30 倍）。**
PaperSqueeze 全部输出的文字、链接、软遮罩指纹与源文件一致，页数与页面几何不变。

### 如何诚实地解读

- **双方都达标的三份文档**质量相当：Nature 完全相同（0.9998）；CycleGAN 中 PaperSqueeze 领先 0.0044，因为它能重编码 PixShift 只能跳过的 308 张带软遮罩图像；Study Notes 中 PixShift 领先 0.0005——这不是清晰度差异：PixShift 把 Display P3 像素转成 sRGB（色度更小、同质量下文件更小），PaperSqueeze 保留了广色域色彩空间，而渲染页面上的 SSIM 无法奖励保留下来的色域。
- **PixShift 在 8 份中有 5 份失败。**不降采样、不碰带遮罩的图像，它就到不了预算，最多花 24 分钟后不产出任何文件。
- **有两份的预算对任何保留矢量层的工具都不可达。**会议手册有 13.3 MB 的轮廓化文字，幻灯片有 1.4 MB 字体和矢量，本身就超过预算。PaperSqueeze 返回与最小可达大小相差 10% 以内、质量最高的一档，CLI 标记为 `OK (over target)`；只有显式的 `raster` 模式才能更进一步。
- **低于 0.9 的最差页**分别来自：一篇图片密集的 arXiv 论文压缩 4.6 倍（最差页是一组扩散模型的纯噪声样本，其纹理经任何重采样都无法保留），以及 251 页扫描件压缩 2.2 倍（每页约 33 KB）。均已目视检查，属于预算本身决定的取舍，不是编码缺陷。
- **度量的局限。**96 DPI 下的 SSIM 对超出屏幕分辩率的细节不敏感，却对亚像素重采样偏移极为敏感；把一张 600 DPI 的照片缩小 6% 会损失约 0.01 SSIM，而在 300% 缩放下肉眼看不出差别。采用它是因为可复现，不是因为它在感知上完备。

## 渲染兼容性

输出用三个独立引擎逐页渲染并与源文件比较：

| 引擎 | 结果 |
| --- | --- |
| MuPDF 1.28（PyMuPDF） | 页面覆盖一致，平均色差 ≤ 1.7/255 |
| Ghostscript 10.07 | 页面覆盖一致 |
| macOS Quartz（预览，CoreGraphics） | 页面覆盖一致 |

这一点很重要：早先基于 `page.replace_image` 的实现，产出的文件 MuPDF 能渲染，但 Ghostscript 和预览会显示空白页或发白的颜色（字典里残留字面量 `null`、ICC 色彩空间丢失）。PixShift 2.0 使用同一个 PyMuPDF 调用，继承了 Ghostscript 下的空白页问题。

## 复现

```bash
# 1. 描述你的语料
cat > corpus.json <<'JSON'
[
  {"name": "paper",  "path": "/data/paper.pdf",  "budget": "6MiB"},
  {"name": "scan",   "path": "/data/scan.pdf",   "budget": "8MiB"}
]
JSON

# 2. 只跑 PaperSqueeze……
uv run scripts/benchmark_corpus.py --manifest corpus.json --out bench/

# 3. ……或与 PixShift 正面对比（通过 uvx 按需安装）
uv run scripts/benchmark_corpus.py --manifest corpus.json --out bench/ --pixshift
```

`bench/results.json` 保存全部测量值；Markdown 表格在结束时打印。重复运行会用 PaperSqueeze 重新压缩，并对 PixShift 已有输出重新评分而不重跑。源文件请放在普通本地路径：位于其他应用沙盒容器内的文件，在从终端复用器运行基准时可能被 macOS 隐私授权弹窗阻塞 `open()`。

`scripts/compare_pdf_quality.py` 每渲染一页就清空 MuPDF 的解码资源缓存。否则在同一进程里交替渲染原件和压缩件时，一个文档的缓存图像可能被交给另一个文档（垃圾回收后对象编号重叠），对实际完全相同的页面报出低至 0.31 的 SSIM。
