# mesh2d/appearance — 原图锁定分层重建与外观门禁

把 see-through 返回的候选 PSD 转换为"可见区域 = 标准化原图像素"的规范化 PSD，
并在发布前验收实际图层重合成与原图的外观一致性。see-through 是扩散式重绘
而非抠图，任何图层（典型如 `face`）都可能被重绘失真；本包保证原图已存在的
可见内容不再由生成模型决定。编排入口见 [pipeline.py](../pipeline.py) `_appearance_stage`。

## 处理链

1. [source_asset.py](source_asset.py)：解码原始字节为标准化 RGBA（EXIF 方向、8-bit、
   sRGB 直通，不做任何风格 / 肤色调整），产出源图哈希与像素哈希。
2. [rebuild.py](rebuild.py)：估计源图 → PSD 画布的等比变换（角色语义层剪影多初值
   + 掩码 IoU 精调；letterbox / 填满画幅 / 包围盒一并作为种子），按运行时遮挡
   归属把原图像素整像素（RGB+Alpha）回填进各图层的可见区域（可见与归属口径
   镜像客户端 cleanAlpha，见下文约束），半透明纱与羽化边缘一并保真；被更高
   不透明图层遮挡的区域保留供应商生成的补全；半透明像素只保留一份源图
   RGBA，清除下层同位置内容，避免重复叠加 Alpha。透明源按 Alpha 识别前景；白底源只
   移除与边界连通的近白背景，封闭浅色区域保留原图像素，避免将皮肤高光、
   白衣与饰品误判为背景。对齐使用填洞剪影（`alignment_foreground`），归属与
   评测使用可见前景（`source_foreground`）。对齐剪影的画布侧只聚合角色语义
   层的 claim，并滤掉近满幅填充与小连通域，避免背景并入层或 speckle 拉偏
   包围盒。输出扁平像素图层 PSD，保持原层名与画布尺寸，客户端 `/1` 描述符
   无需变化。
3. [gate.py](gate.py)：从**保存后的 PSD 字节**重新解析并重合成实际图层
   （`ignore_preview=True`，禁止用内嵌预览顶替），与原图逐像素比较：
   全局内部前景与脸部区域的 ΔE00（平均 ≤1、P95 ≤3），脸区色差 >3 的
   4 连通区域达到 9px 即失败，避免局部斑块被均值与分位数掩盖。颜色比较包含
   半透明前景；同时比较源图与合成 Alpha（容差 2），脸区不得偏离，全局内部
   偏离率不得超过 0.1%，透明源的全透明区不得新增可见像素。另检查内部前景
   覆盖率；轮廓边缘带不参与色差比较；
   必须存在归一名为 `face` 的脸底且脸区非空，否则直接判
   `FACE_APPEARANCE_MISMATCH`，不以全局或仅有五官层通过放行。图层包围盒
   超出画布时按交集写入脸部区域，避免负坐标广播错误。
4. [trace.py](trace.py)：诊断产物（原图、供应商 PSD、重建 PSD、合成图、报告）
   按 `data_dir/appearance-trace/u<user_id>/<时间-唯一标识>/` 落盘，写完后原子发布
   目录，清理只处理完整目录，保留最近 20 份。

任一环节失败抛 `AppearanceError`（`SOURCE_ALIGNMENT_FAILED` /
`UNSUPPORTED_LAYER_SEMANTICS` / `PSD_COMPOSITE_MISMATCH`），门禁未通过不写库、
不激活，保留上一版合格外观。公共入口为 `load_source_appearance` →
`rebuild_source_locked` → `run_gate`（见 [__init__.py](__init__.py)）；诊断产物
由 pipeline 经 [trace.py](trace.py) 落盘，不在本包内提供独立 CLI。CPU 处理与 PNG
编码在线程执行；超预算或取消不发布，等待当前线程收尾后再退出，避免遗留后台
处理任务。超时不是强杀线程，也不保证整个收尾过程在预算内完成。

## 容易改错的约束

- **输出 PSDImage 必须用 RGBA 模式创建。** RGB 模式 PSD 写入时会把图层
  Alpha 展平成不透明，客户端与验收合成都会拿到矩形级实色块。
- **PSD 文件记录、psd-tools 迭代与 ag-psd children 同为自底向上**，客户端
  Rigger 按 children 数组序绘制（后画在上）；按运行时自底向上顺序 append
  （psd-tools 后 append 在上）即还原运行时层序。
- **遮挡归属必须镜像客户端运行时层序，不是 PSD 面板顺序。** see-through
  的面板顺序把 `face` 放在五官之上；客户端 Rigger 会把头部五官按
  `HEAD_FEATURE_ORDER` 重排（face 最底、front hair 最顶），且 `normName`
  先做整名归一（`mouth` / `mouth 2` → `mouth_open`、`eyelash_c` →
  `eye_close` 等），层序与脸部识别都以归一后的名为准。rebuild.py 的
  `base_name` 镜像该两阶段语义（normName 整名归一 → 后缀剥离 → baseName
  别名），与
  [vendor/rigger.js](../../../../../../client/renderer/modules/character/rendering/2d/puppet/vendor/rigger.js)
  必须同步修改；归一化只执行一次（注意 `mouth-r` 这类带侧向后缀的名两边都不归一为
  `mouth_open`，保持一致）。
- **归属口径必须镜像客户端 cleanAlpha**：buildRig 先把每层 alpha > 16 的
  4 连通分量按 ≥40px 过滤、保留分量外扩 3px，之外的像素运行时清零；没有
  任何 ≥40px 分量的层原样保留。rebuild 的 claim 与对齐剪影聚合都用同一
  口径，否则顶层 speckle 噪声会在重建时抢占可见像素、又在运行时被客户端
  删除，让下层生成内容透出顶替原图；角色外噪声还会拉偏对齐包围盒（门禁
  合成不含 cleanAlpha，拦不住这类偏差）。
- **客户端渲染只读图层 imageData、忽略 PSD 蒙版与不透明度**，因此蒙版和
  不透明度必须烘焙进像素 Alpha（禁用的蒙版跳过，范围外使用其默认背景值），运行时所见才与门禁
  合成一致。
- **近白色不等于背景**：不透明白底图只剔除从画布边界可达的近白像素。
  封闭白色背景和角色内部白色内容无法仅凭颜色可靠区分，均保留原图像素
  并参与验收；需要精确的透明孔洞时使用带 Alpha 的原图。透明原图的孔洞
  清除所有图层同位置生成内容，保留透明；对齐剪影单独填洞。
- **对齐剪影不等于发布剪影**：画布侧只聚合角色语义层 claim，并排除近满幅
  层与小连通域；归属与门禁仍按完整运行时 claim / 合成结果执行。背景并入
  角色层且连通成一大块时，对齐仍可能失败——这是明确失败而不是静默错位。

## 验证

- 故障样本回归：用历史灰脸任务的原图 + 供应商 PSD 走公共入口
  `load_source_appearance` → `rebuild_source_locked` → `run_gate`，门禁必须
  通过；旧灰块产物必须被脸区连续色差检查拒绝。另用客户端 ag-psd 与
  Rigger 的清理、嘴层归类和分层装配验证中性合成，不能用 PSD 内嵌预览代替。
- ΔE00 实现已对齐 scikit-image `deltaE_ciede2000`（Sharma 全量测试向量）。
- 修改对齐、归属或门禁口径后，必须重跑故障样本，不得只跑单测或静态检查。
