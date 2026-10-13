# Relay Imagegen

[English README](README_EN.md)

解决 Codex 使用中转站生图时，分辨率、比例、提示词记录和输出记录不容易稳定控制的问题。

使用中转站时还在抱怨 Codex 对话内置生图图片比例不对？要求它 16:9，结果它还是输出 1:1？要求它输出 4K，结果输出的还是低分辨率？那你可以试试这个 Skill。

配置和使用极速版：

给 Codex 发：

```text
Codex，帮我安装一下这个 Skill：https://github.com/AwakeFantasy/relay-imagegen
```

使用：

```
$relay-imagegen 跑一张 16:9、4K 的风景图看看效果
```

效果：

![image-20260527214630864](README/image-20260527214630864.png)

为什么会有对话内置生成效果不稳定的问题呢？因为对话里的原生生图更偏“让模型按自然语言理解你的要求”，Codex 不一定会把“4K、16:9、横图”稳定地转换成底层命令参数。它可能只是把这些要求写进 prompt，让模型尽量遵守，但这不等于真的传了 `--size 3840x2160`。结果就可能被默认尺寸、工具策略或模型理解偏差带歪，跑出低分辨率、方图、比例不准的图。

极简的介绍就到此结束了，下面是这个 Skill 详细的介绍：

Relay Imagegen 是一个 Codex Skill，用于通过兼容 OpenAI 接口的中转站或代理生成、编辑图片，并自动保存提示词和不含密钥的出图记录。

它的目标是让中转站出图流程尽可能简单、可复用、可追溯：

- 支持使用 `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst` 和 `gpt-image-2`
- 通过独立配置保存一个或多个生图中转站地址、密钥和模型
- 多个中转站按 GUI 中的顺序尝试；连接、429、5xx 等故障可自动切换到下一个源
- 推荐使用 `prompts/*.txt` 保存和复用提示词
- 每次成功生成后自动写入不含密钥的 `.meta.json` 记录文件
- `.meta.json` 会保存当次提示词快照，方便复盘和复现
- 默认分辨率：`2560x1440`
- 默认模型：`gpt-image-2.5-flare`
- 默认质量：`high`
- 默认输出到当前目录下的 `generated/`
- 支持上传前自动缩小参考图，减少传图耗时和失败概率

## 适用场景

当你遇到这些情况时，可以使用这个 Skill：

- 你使用中转站生成图片，而不是 OpenAI 官方 endpoint
- 你希望把提示词保存成文件并复用
- 你希望每次出图都自动保存提示词快照、模型、尺寸、输入图和输出路径
- 你不希望设置系统级 `OPENAI_API_KEY`
- 你想用本地参考图进行角色、构图或风格编辑
- 你希望默认生成较高分辨率图片，例如 `2560x1440`

## 工作方式

Codex 会调用本地 Python 脚本出图。脚本会：

1. 从 Skill 根目录的本地配置读取一个或多个生图中转站信息。
2. 使用 OpenAI Python SDK 请求图片生成或编辑接口。
3. 仅在传输失败、429 或 5xx 时按顺序切换中转站。
4. 验证输出图片尺寸，并写入不包含密钥的运行记录。

真实 key 不会出现在命令行参数、输出日志或运行记录中。

## 环境要求

- Python 环境已安装 `openai` 和 `Pillow`
- Python 3.10 或更高版本
- 一个支持图片生成模型的 OpenAI 兼容中转站
- 可选：Pillow，用于参考图预处理和输出尺寸检查

## 安装

将整个仓库放到 Codex Skill 目录：

```text
~/.codex/skills/relay-imagegen
```

Windows 通常为：

```text
C:\Users\<你的用户名>\.codex\skills\relay-imagegen
```

也可以从 GitHub 克隆：

```powershell
git clone https://github.com/AwakeFantasy/relay-imagegen.git "$HOME/.codex/skills/relay-imagegen"
```

安装后，当你在 Codex 中提到中转站出图、保存提示词、出图记录或 `$relay-imagegen` 时，Codex 就可以使用这个 Skill。

## 最快开始

首次使用时，在 Skill 根目录双击 `configure.pyw`，或运行下面的命令，填写 Relay 地址、访问密钥并选择模型。之后 Codex 会直接调用 Python 脚本出图，不会每次打开 GUI。

```powershell
$setup = "$HOME/.codex/skills/relay-imagegen/scripts/setup.py"
python $setup gui
```

### 1. 准备提示词文件

在当前项目下创建：

```text
prompts/test.txt
```

将你的出图提示词写进去。

### 2. 先进行 dry-run

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill generate --prompt-file prompts/test.txt --name test --dry-run
```

`--dry-run` 不会请求中转站，也不会产生费用，只会显示将要执行的非敏感参数和输出位置。

### 3. 正式出图

```powershell
python $skill generate --prompt-file prompts/test.txt --name test --force
```

默认输出：

```text
generated/test-YYYYMMDD-HHMMSS-2k.png
generated/test-YYYYMMDD-HHMMSS-2k.meta.json
```

## 配置

在 Skill 根目录双击 `configure.pyw`，或按上面的命令打开配置窗口。界面可以添加、删除、启用、停用和调整多个 Relay 源的顺序；之后生图会自动使用，无需再次打开 GUI。

本地配置保存在 Skill 根目录的 `config.json`，不会包含在仓库中。通常不需要手动编辑。

旧的单源配置仍然兼容。多源配置由 GUI 自动保存，结构大致如下：

```json
{
  "sources": [
    {"name": "primary", "base_url": "https://relay-a.example/v1", "api_key": "...", "model": "gpt-image-2.5-flare"},
    {"name": "backup", "base_url": "https://relay-b.example/v1", "api_key": "...", "model": "gpt-image-2.5-sunburst"}
  ]
}
```

路由是保守的：400 等请求参数错误不会自动切换；超时也可能发生在上游已经接受请求之后，因此切换备用源有重复生成或重复计费的可能。每次成功运行的 sidecar 会记录实际使用源和非敏感失败摘要。

本地 Agent 可以先用 `python $skill --list-sources` 查看已启用的源名和模型，再按任务选择并通过 `--source <名称>` 指定。Flare 适合日常快速生成；对编辑精度要求较高时优先 Sunburst。明确指定模型或源时优先遵循用户要求。

图片流式为可选功能：添加 `--stream` 后，局部预览保存为最终图旁边的 `*.partial-N.<格式>`，最终图片仍写入指定输出路径。可用 `--partial-images 0..3` 设置预览数量，默认 1。中转站需要转发图片 SSE 事件；流中断后可能无法确定服务端是否已完成请求，因此不会自动换源重试。

## 生成图片

### 使用提示词文件生成图片

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill generate `
  --prompt-file prompts/test.txt `
  --name test `
  --force
```

默认等价于：

```text
model   = gpt-image-2.5-flare
size    = 2560x1440
quality = high
output  = generated/
```

### 直接传入简短提示词

```powershell
python $skill generate `
  --prompt "A quiet cinematic room with warm soft lighting." `
  --name room `
  --force
```

长提示词、中文提示词或希望保留复用的提示词，建议使用 `--prompt-file`。成功出图后，提示词内容也会写入 `.meta.json` 的 `prompt_snapshot` 字段。

## 使用参考图编辑图片

### 单张参考图

```powershell
python $skill edit `
  --image C:/path/to/reference.jpg `
  --prompt-file prompts/edit.txt `
  --name edit-test `
  --prepare-image `
  --force
```

### 多张参考图

```powershell
python $skill edit `
  --image C:/path/to/composition.png `
  --image C:/path/to/character.jpg `
  --prompt-file prompts/edit.txt `
  --name final `
  --prepare-image `
  --force
```

适合：

- 一张图作为场景或构图参考
- 一张图作为人物设定或服装参考
- 生成“现实照片环境中出现二次元角色”之类的合成画面

## 参考图预处理

高分辨率参考图可能上传慢、占用更高，或者增加中转站请求失败概率。

使用：

```powershell
--prepare-image
```

会在上传前将参考图最长边压缩到默认 `2048` 像素。

也可以自行指定最大边长：

```powershell
python $skill edit `
  --image C:/path/to/large-reference.jpg `
  --prompt-file prompts/edit.txt `
  --max-input-edge 1536 `
  --name prepared-edit `
  --force
```

默认情况下，处理后的上传副本只保存在临时目录中，运行结束后会自动删除，不会污染当前项目。

如果你想保留这些副本用于调试，可以加：

```powershell
--keep-prepared
```

此时副本会保存在：

```text
generated/relay_prepared/
```

原始图片不会被修改。

## 输出文件和运行记录

如果没有指定 `--out`，图片默认输出为：

```text
generated/<名称>-YYYYMMDD-HHMMSS-2k.png
```

例如：

```text
generated/character-chair-20260527-183000-2k.png
```

每次成功生成后，会自动写入同名记录文件：

```text
generated/character-chair-20260527-183000-2k.meta.json
```

记录内容包括：

- 使用的是 `generate` 还是 `edit`
- 模型
- 请求尺寸和输出尺寸
- 图片质量
- 提示词文件位置
- 当次提示词快照
- 输入参考图位置
- 预处理图片尺寸；默认不保留临时预处理图片路径
- 实际使用的中转站、尝试顺序和非敏感失败摘要
- 耗时

记录文件不会包含 API key。

## 常用参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `generate` / `edit` | 生成新图或使用参考图编辑 | 必填 |
| `--prompt-file` | 从文本文件读取提示词，推荐使用 | 无 |
| `--prompt` | 直接传入简短提示词 | 无 |
| `--image` | `edit` 模式的参考图，可重复传入 | 无 |
| `--model` | 覆盖配置中的生成模型 | 配置中选择的模型 |
| `--source` | 指定优先使用的已配置源；可用 `--list-sources` 查看名称 | 首个启用源 |
| `--list-sources` | 只显示源名称、模型和启用状态，不显示 key 或 URL | 关闭 |
| `--stream` | 保存生成过程中的局部预览图 | 关闭 |
| `--partial-images` | 流式模式下的局部预览数量（0-3） | `1` |
| `--name` | 自动输出文件名的名称部分 | 根据模式或 prompt 文件生成 |
| `--out` | 指定完整输出路径 | 自动命名 |
| `--output-dir` | 自动命名输出目录 | `generated` |
| `--size` | 输出分辨率 | `2560x1440` |
| `--quality` | 输出质量 | `high` |
| `--timeout` | 超时时间，单位为秒 | `600` |
| `--prepare-image` | 上传前压缩参考图 | 关闭 |
| `--max-input-edge` | 指定参考图最大边长，同时开启预处理 | `2048` |
| `--keep-prepared` | 保留预处理上传副本到 `generated/relay_prepared/` | 关闭 |
| `--dry-run` | 只检查命令和输出位置，不实际请求 | 关闭 |
| `--force` | 允许覆盖已有输出文件 | 关闭 |

## 安全说明

- 不要把真实 API key 作为命令行参数传入。
- 不要把真实配置文件提交到 GitHub。
- 不需要把 `OPENAI_API_KEY` 永久写入用户或系统环境变量。
- key 只在运行时读取，不会显示或保存到运行记录。
- `.meta.json` 运行记录不会包含 key。

## 故障排查

### 没有找到配置

首次设置或更新配置时，打开本地 GUI：

```powershell
$setup = "$HOME/.codex/skills/relay-imagegen/scripts/setup.py"
python $setup gui
```

### 输出尺寸不是默认尺寸

默认请求尺寸是：

```text
2560x1440
```

如果中转站或模型不支持该尺寸，生成请求可能失败，或者尺寸验证不通过。此时需要检查你所用中转站对模型和尺寸的支持情况。

### 请求时间过长

可以提高超时时间：

```powershell
python $skill generate --prompt-file prompts/test.txt --timeout 900 --name test --force
```

## 仓库结构

```text
relay-imagegen/
  README.md
  README_EN.md
  README/
  SKILL.md
  LICENSE
  agents/
    openai.yaml
  scripts/
    relay_imagegen.py
    setup.py
```

以下本地生成内容不会包含在公开仓库中：

```text
config.json
generated/
*.meta.json
```

## License

[MIT License](LICENSE)
