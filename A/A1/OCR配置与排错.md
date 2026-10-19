# PaddleOCR 配置与排错

> 本文记录 PaddleOCR 的完整配置方式、每项配置背后的理由、以及踩过的坑。
> 目标是「换一台机器也能复现」+「网上教程对不上时知道该查哪里」。
>
> 相关代码位置：
> - 调用逻辑：`fetch/techfin_ocr.py`
> - 参数配置块：`config/topic_config.py:847-859`
> - 流水线编排：`run_all.py:35-43`
> - 简述版说明：`README.md:305-324`

---

## 1. 环境

本机（Windows 11 + Python venv）装的是 **CPU 版 `paddlepaddle` wheel**, `is_compiled_with_cuda()` 返回 `False`. 因此:

- `OCR_DEVICE = "cpu"`（`topic_config.py:856`）
- 选型、页数上限、DPI 这些参数**全部围绕"CPU 很慢"这个约束来定**（见 §3、§5）

由于CPU 版 wheel 里没有 CUDA kernel, 若想启用 GPU, 必须先卸载 CPU 版再重装 GPU 版. 再将配置项设为 `device="gpu"` 就行.

```bash
pip uninstall -y paddlepaddle
pip install paddlepaddle-gpu==<version> -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
```

---

## 2. 模型下载与缓存

### 2.1 下载源设置

PaddleOCR 3.x 的模型由 **PaddleX** 负责下载，默认源是 `huggingface`（见 `paddlex/utils/flags.py`）。**本机访问 huggingface 不可达**，直接跑会卡在下载阶段。百度自家的 **BOS** 源可达，所以第一步先切源：

```bash
set PADDLE_PDX_MODEL_SOURCE=BOS        # Windows cmd
# export PADDLE_PDX_MODEL_SOURCE=BOS   # bash / PowerShell 用这个
```

本项目未设置 `PADDLE_HOME` / `PADDLEOCR_HOME`，缓存路径完全由 PaddleX 默认值决定。

### 2.2 一次性下载模型

`techfin_ocr.py:13-15` 与 `README.md:309-312` 都记了这条命令。构造一次 `PaddleOCR` 就会触发下载：

```bash
set PADDLE_PDX_MODEL_SOURCE=BOS
python -c "from paddleocr import PaddleOCR; PaddleOCR(lang='ch', ocr_version='PP-OCRv5', device='cpu', text_detection_model_name='PP-OCRv5_mobile_det', text_recognition_model_name='PP-OCRv5_mobile_rec', use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)"
```

### 2.3 依赖项

```
C:\Users\username\.paddlex\official_models\
├── PP-OCRv5_mobile_det   4.8M   ← 检测
├── PP-OCRv5_mobile_rec   17M    ← 识别
├── PP-OCRv5_server_det   85M    ← 当前配置未引用
└── PP-OCRv5_server_rec   81M    ← 当前配置未引用
```

要点：

- 引用内容不在项目目录下。因此 **git 不管它**，换机器要另行迁移
- 代码里不指定 `det_model_dir` / `rec_model_dir`，只给模型名字，路径解析全部交给 PaddleX 默认缓存。这样"缓存里已有就离线复用，没有就去下载"的逻辑不用自己写
- 以下两个 server 模型不用管，是否删除都不影响. 如果需要删除，请运行以下命令：

  ```bash
  rm -rf ~/.paddlex/official_models/PP-OCRv5_server_det ~/.paddlex/official_models/PP-OCRv5_server_rec
  ```


---

## 3. 参数配置

配置块在 `topic_config.py:847-859`，初始化实参在 `techfin_ocr.py:117-123`：

```python
# topic_config.py:847-859
OCR_ENABLED     = True
OCR_LANG        = "ch"
OCR_VERSION     = "PP-OCRv5"
OCR_DET_MODEL   = "PP-OCRv5_mobile_det"
OCR_REC_MODEL   = "PP-OCRv5_mobile_rec"
OCR_DEVICE      = "cpu"
OCR_DPI         = 200
OCR_MAX_PAGES   = 50
OCR_PREFIX      = "ocr_"
```

```python
# techfin_ocr.py:117-123
_OCR = PaddleOCR(lang=cfg.OCR_LANG, ocr_version=cfg.OCR_VERSION,
                 device=cfg.OCR_DEVICE,
                 text_detection_model_name=cfg.OCR_DET_MODEL,
                 text_recognition_model_name=cfg.OCR_REC_MODEL,
                 use_doc_orientation_classify=False,
                 use_doc_unwarping=False,
                 use_textline_orientation=False)
```

| 参数              | 值                     | 值用法                                           | 修改后果                            |
| --------------- | --------------------- | --------------------------------------------- | ------------------------------- |
| `OCR_ENABLED`   | `True`                | 总开关。`techfin_ocr.py:367-369` 读到 `False` 会直接中止 | 设 `False` 可临时停用 ⑤ 而不改代码         |
| `OCR_LANG`      | `"ch"`                | 语料全是中文政府公报/名单                                 | 改 `en` 会显著掉中文准确率                |
| `OCR_VERSION`   | `"PP-OCRv5"`          | 当前最新一代，中文效果最好                                 | 换旧版（v4/v3）精度下降                  |
| `OCR_DET_MODEL` | `PP-OCRv5_mobile_det` | **关键决策**，server 版 CPU 上慢到不可用                  | 改 server → 单页数十秒~分钟             |
| `OCR_REC_MODEL` | `PP-OCRv5_mobile_rec` | 同上                                            | 同上                              |
| `OCR_DEVICE`    | `"cpu"`               | 本机无 CUDA                                      | 有 GPU 时改 `"gpu"`，需先换 wheel      |
| `OCR_DPI`       | `200`                 | PDF 渲染分辨率。200 在"够清晰"和"够快"之间                   | 调到 300 更准但渲染+推理线性变慢；调到 150 小字易糊 |
| `OCR_MAX_PAGES` | `50`                  | 单个附件最多 OCR 的页数，防一个超大件拖垮整轮                     | 调大遇到几百页附件会卡很久                   |
| `OCR_PREFIX`    | `"ocr_"`              | 每轮产物前缀 → `ocr_log` / `ocr_index.csv`          | 影响产物文件名                         |

### 3.1 `use_*` 配置项 

这三个默认都是 `True`，我们全部关掉，因为对"政府公报扫描件"这个具体场景收益低、耗时占比高：

| 参数                             | 功能                           | 原因                        |
| ------------------------------ | ---------------------------- | ------------------------- |
| `use_doc_orientation_classify` | 整页方向分类（判断这张图是不是旋转了 90°/180°） | 扫描件基本都是正的；歪页可由 3.2 的行方向兜底 |
| `use_doc_unwarping`            | 文档弯曲矫正（把卷曲/透视变形的照片拉平）        | 是扫描仪输出，不是手机拍的，不存在弯曲       |
| `use_textline_orientation`     | 单行文本方向分类                     | 中文公版式横排，极少出现竖排/倒排行        |


### 3.2 API 对照

本项目用的是 PaddleOCR 3.x 的新 API. 可与 2.x 版本的API文档对照

| 用途      | 2.x                     | 3.x （**本项目**）                                       |
| ------- | ----------------------- | --------------------------------------------------- |
| 指定设备    | `use_gpu=True/False`    | `device="gpu"/"cpu"`                                |
| 文本行方向分类 | `use_angle_cls=True`    | `use_textline_orientation=True`                     |
| 检测模型目录  | `det_model_dir="/path"` | `text_detection_model_name="PP-OCRv5_mobile_det"`   |
| 识别模型目录  | `rec_model_dir="/path"` | `text_recognition_model_name="PP-OCRv5_mobile_rec"` |
| 推理调用    | `ocr.ocr(img)`          | `ocr.predict(img)`（新）/ `ocr.ocr(img)`（**已废弃**）      |
| 取文本     | `result[0][i][1][0]`    | 每页结果对象的 `rec_texts` 列表                              |
| 取置信度    | `result[0][i][1][1]`    | 每页结果对象的 `rec_scores` 列表                             |

对应的取结果代码在 `techfin_ocr.py:188-203`（`_extract_list(r, "rec_texts")` / `"rec_scores"`），并且 `_extract_list`（`:175-185`）做了**两种结果形态的兼容**（`{..'res':{key:..}}` 与扁平），以应对不同小版本的差异。


---

## 4. 独立OCR 

流水线里 OCR 是**单独一步 ⑤**，不是塞在 ② 抓取里顺手做的。原因写在 `techfin_ocr.py:8-9`：

> ② 会跳过"已成功抓取"的条目（含已下载的扫描件），故补 OCR 必须在 ② 之外单独跑一遍。

展开说：② 的幂等策略是"已完成的不重复做"。而扫描件在 ② 眼里"已成功下载"——于是**历史扫描件永远进不了 ② 的处理队列**，OCR 也就永远不会被执行。把 OCR 拆出来独立跑，它有自己的筛选条件（`is_scanned` + 未 `ocr_applied`，见 `techfin_ocr.py:226-238`）和自己的幂等标记，才能对着**存量缓存**批量补。

**副产品**：② 因此不必依赖 paddle 这套重依赖，依赖面收窄，跑 ② 时不用等模型加载。

**注意流水线顺序**（编号是历史沿革，不是执行顺序. 平时使用直接看运行脚本）：

```
① 采集 → ② 抓取 → ⑤ OCR → ④ 清洗 → ③ 抽取
```

⑤ 在 ② 之后、④ 之前。OCR 结果写回 `body_cache/`（同名 `.txt` + 更新 JSON 字段，`techfin_ocr.py:266-275`），不是另存一份语料。因此下游 ③ 抽取走 `att.get("text_path")` 读附件 `.txt`（`extract/techfin_extract_points.py:341-357`）时自动吃到 OCR 文本，无需改 ③。④ 清洗本身不感知 OCR。

---

## 5. 性能及模型选择

### 5.1 server vs mobile

| 模型                             | 权重体积       | CPU 单页耗时                            | 结论       |
| ------------------------------ | ---------- | ----------------------------------- | -------- |
| `PP-OCRv5_server_det` + `_rec` | 85M + 81M  | **数十秒 ~ 分钟**；29 页 PDF 跑 >10 分钟出不来一页 | 不可用      |
| `PP-OCRv5_mobile_det` + `_rec` | 4.8M + 17M | **约 4s/页**                          | 中文识别质量足够 |

`PaddleOCR(ocr_version="PP-OCRv5")` 的默认是 server 版——所以"什么都不指定直接跑"正好踩中最慢的那条路。这是 3. 里非要把两个 `text_*_model_name` 显式写死的唯一原因。

### 5.2 四条降本手段

1. **轻量模型**：mobile 版（§5.1）
2. **只处理扫描件**：② 已把原生 PDF 抽好文本，⑤ 只碰 `is_scanned=True` 的（`techfin_ocr.py:233-234`）
3. **幂等只跑一次**：`ocr_applied=True` 标记，重跑自动跳过；`--refresh` 才强制重来（`:235-236`）
4. **限页**：`OCR_MAX_PAGES=50`，`render_pdf_pages` 只渲染 `min(total, 50)` 页（`:149`）

### 5.3 有意**没有**做的优化

- 无并发 / 无多进程 —— 单进程顺序跑，进度可见、Ctrl-C 可控
- 无 per-page 超时 —— 超时打断一次推理没有意义（模型已经开始算了）
- 无重试 —— OCR 是纯本地确定性计算，失败通常不是抖动
- 无图像二值化 / 灰度 / 裁剪预处理 —— 瓶颈在模型推理，不在图像处理；加预处理反而是净亏损

成本控制靠"选对模型 + 只处理该处理的 + 只跑一次 + 缓存"，不靠并行。

---

## 6. 踩坑清单 + 排错对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| 单页几十秒甚至分钟级 | 用了默认的 **server** 模型 | 显式指定 `PP-OCRv5_mobile_det` / `PP-OCRv5_mobile_rec`（§3） |
| 卡在下载、超时、进度不动 | 走了默认 **huggingface** 源，本机不可达 | `set PADDLE_PDX_MODEL_SOURCE=BOS` 后重试（§2.1） |
| `TypeError: unexpected keyword argument 'use_gpu'` | 抄了 **2.x** 教程 | 换 `device=`（§3.2） |
| `... 'use_angle_cls'` 同类报错 | 同上 | 换 `use_textline_orientation=`（§3.2） |
| `AttributeError: 'PaddleOCR' object has no attribute 'ocr'` | 3.x 已废弃 `.ocr()` | 用 `.predict()`（§3.2） |
| 结果取出来是空的 / 结构对不上 | 还在按 2.x 的嵌套下标取 | 改取 `rec_texts` / `rec_scores`（§3.2） |
| 扫描件附件一直没有正文 | ⑤ 没跑，或该附件已被标记过 | 跑 `python fetch/techfin_ocr.py`；必要时加 `--refresh` |
| 打印"无法导入 paddleocr" | 缺包，或模型没下载 | 见 §1（装包）与 §2（下模型） |
| 打印"`OCR_ENABLED=False`，未启用 OCR" | 总开关被关了 | 改 `topic_config.py:849` |
| 一轮跑完但某个附件 `status` 非 ok | PDF 打开/渲染失败，或缺 `pypdfium2`/`numpy` | 看 `ocr_index.csv` 的 `status` 列与 `ocr_log` 输出 |
| 换机器后所有附件都"重新下载模型" | `~/.paddlex/` 没跟过来 | 见 §7 |
| 自检脚本报 `pypdfium2 has no attribute '__version__'` | `pypdfium2` 不暴露该属性 | 用 `importlib.metadata.version("pypdfium2")`（§7） |

**排错时先看三处**：脚本回显的 `[OCR] 正在加载 ...（cpu）模型` 一行、当轮产物 `ocr_index.csv` 的 `status` 列、以及 `ocr_log`。

---

## 7. 换机器 / 离线迁移

步骤: 

1. **搬依赖**
   ```bash
   pip install -r requirements.txt
   ```
   （清单见仓库根 `requirements.txt`，版本钉到本机实测值）

2. **搬模型**（离线也能用，前提是这一步做了）
   ```
   把源机器 C:\Users\username\.paddlex\official_models\
   拷到目标机器同位置的 ~/.paddlex/official_models/
   ```
   只需搬在用的 `PP-OCRv5_mobile_det` 与 `PP-OCRv5_mobile_rec` 两个（约 22M）。拷完后不设 `PADDLE_PDX_MODEL_SOURCE` 也能跑（缓存命中就不下载）；设了也无害。

3. **验证**
   ```bash
   python -c "import importlib.metadata as md, paddle; \
     print(*(md.version(p) for p in ('paddleocr','paddlex','pypdfium2'))); \
     print('CUDA:', paddle.device.is_compiled_with_cuda())"
   # 预期：3.7.0 3.7.2 5.14.0
   #       CUDA: False
   ```
   > 注意：`pypdfium2` **没有** `__version__` 属性，别写 `pypdfium2.__version__`（会 `AttributeError`）。
   > 统一用 `importlib.metadata.version("包名")` 取版本，对四个包都成立。

   再跑一个附件的冒烟：
   ```bash
   python A/A1/fetch/techfin_ocr.py --limit 1
   ```
   看到 `[OCR] 模型就绪（Ns）` 与汇总行 `平均 X.Xs/页`，且 `body_cache` 里对应 JSON 出现 `"ocr_applied": true`、`"text_method": "paddleocr"`，即迁移成功。

---

## 附录 A：本机实测版本（2026-10-16，取自 `.venv`）

| 包 | 版本 | 说明 |
|---|---|---|
| `paddleocr` | 3.7.0 | 高层 API（3.x 新 API） |
| `paddlepaddle` | 3.2.0 | 推理引擎，**CPU 版 wheel** |
| `paddlex` | 3.7.2 | 模型下载/路径解析的实际执行者 |
| `pypdfium2` | 5.14.0 | PDF → 图像渲染（**本项目单独依赖**，非 Paddle 系） |
| `numpy` | 2.3.5 | 图像数组 |
| `opencv-contrib-python` | 4.10.0.84 | 由 PaddleX 引入；注意与 `opencv-python` 冲突，只保留 contrib |
| `pillow` | 12.3.0 | 由 PaddleX 引入 |
| `pyclipper` / `shapely` | 1.4.0 / 2.2.0 | 检测后处理（多边形） |

## 附录 B：已知未决项

- **缓存路径不可配置**：`PADDLE_HOME` / `PADDLEOCR_HOME` 全仓无设置，路径由 PaddleX 默认值决定。若将来要把它挪进项目目录或指定盘符，需在此处补充做法。
- **无 requirements 锁定时的历史问题**：本文档诞生前，仓库根本没有依赖清单，版本只存在于 `.venv`。现已补 `requirements.txt`（本附录 A 即其依据）。
- **无速度基准回归**：`~4s/页` 是一次性观测值，换机器/换模型后无自动对比。若日后要调优，可基于 `ocr_index.csv` 的 `seconds` / `ocr_pages` 两列做基线。
