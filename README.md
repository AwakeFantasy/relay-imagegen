# Relay Imagegen

[English README](README_EN.md)

Relay Imagegen 是一个 Codex Skill，用于通过兼容 OpenAI 接口的中转站或代理生成、编辑图片，并且不需要把 `OPENAI_API_KEY` 持久写入系统环境变量。

它的目标是让 4K 出图流程尽可能简单：

- 默认原生 4K 分辨率：`3840x2160`
- 默认模型：`gpt-image-2`
- 默认质量：`high`
- 默认输出到当前目录下的 `generated/`
- 每次成功生成后自动写入不含密钥的 `.meta.json` 记录文件
- 默认优先读取当前 ccswitch 的 Codex 供应商配置
- 没有 ccswitch 时，也支持私有 JSON 配置文件
- 支持上传前自动缩小参考图，减少传图耗时和失败概率

## 适用场景

当你遇到这些情况时，可以使用这个 Skill：

- 你使用中转站生成图片，而不是 OpenAI 官方 endpoint
- 你希望生成 `3840x2160` 的 4K 图片
- 你不希望设置系统级 `OPENAI_API_KEY`
- 你使用 ccswitch 切换 Codex 中转站，希望直接复用其中的 key 和 endpoint
- 你想用本地参考图进行角色、构图或风格编辑
- 你希望每次出图保留模型、尺寸、输入图、耗时等运行记录

## 工作方式

Relay Imagegen 当前是 Codex 自带图片生成脚本的包装层：

```text
~/.codex/skills/.system/imagegen/scripts/image_gen.py
```

它负责：

1. 从 ccswitch 或私有配置文件读取中转站信息。
2. 只在当前子进程运行期间注入 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。
3. 调用 Codex 自带的 imagegen 脚本生成或编辑图片。
4. 验证输出图片尺寸。
5. 写入不包含密钥的 sidecar 记录文件。

真实 key 不会出现在命令行参数、输出日志或 sidecar 中。

## 环境要求

- 已安装 Codex，并且带有系统 `imagegen` Skill
- Python 3.10 或更高版本
- 一个支持图片生成模型的 OpenAI 兼容中转站
- 可选：ccswitch，用于自动读取当前 Codex provider
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

安装后，当你在 Codex 中提到中转站出图、4K 生图、ccswitch、`api_key.json` 或 `$relay-imagegen` 时，Codex 就可以使用这个 Skill。

## 最快开始

如果你已经使用 ccswitch 为 Codex 配置了中转站，那么通常不需要额外写入 key。

### 1. 检查配置

```powershell
$setup = "$HOME/.codex/skills/relay-imagegen/scripts/setup.py"
python $setup --check
```

成功时会看到类似输出：

```text
CCSWITCH_DB=C:\Users\you\.cc-switch\cc-switch.db
CCSWITCH_PROVIDER=Your Provider
API_KEY=sk-...xxxx
BASE_URL=https://relay.example/v1
MODEL=gpt-image-2
```

密钥只会脱敏显示。

### 2. 准备提示词文件

在当前项目下创建：

```text
prompts/test.txt
```

将你的出图提示词写进去。

### 3. 先进行 dry-run

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill generate --prompt-file prompts/test.txt --name test --dry-run
```

`--dry-run` 不会请求中转站，也不会产生费用，只会显示将要执行的非敏感参数和输出位置。

### 4. 正式出图

```powershell
python $skill generate --prompt-file prompts/test.txt --name test --force
```

默认输出：

```text
generated/test-YYYYMMDD-HHMMSS-4k.png
generated/test-YYYYMMDD-HHMMSS-4k.meta.json
```

## 配置方式

Relay Imagegen 支持两种方式：

1. 自动读取 ccswitch 当前 Codex provider，推荐给已使用 ccswitch 的用户。
2. 使用私有 JSON 文件，适合没有安装 ccswitch 或需要单独配置图片中转站的用户。

### 默认读取顺序

如果没有显式提供 `--config`，脚本按下列顺序读取配置：

1. 当前 ccswitch 的 `codex` provider：`~/.cc-switch/cc-switch.db`
2. 环境变量 `RELAY_IMAGEGEN_CONFIG` 指向的 JSON 文件
3. 当前项目下的 `photo/api_key.json`
4. 当前项目下的 `.secrets/image_api.json`
5. 当前项目下的 `.secrets/relay_imagegen.json`
6. 当前 Skill 下的 `.secrets/config.json`
7. Windows 下的 `%APPDATA%/relay-imagegen/config.json`
8. `~/.config/relay-imagegen/config.json`
9. `~/.relay-imagegen.json`

如果你传入：

```powershell
--config C:/path/to/config.json
```

则会直接使用该配置，不尝试 ccswitch。

## 使用 ccswitch

### ccswitch 自动读取什么

默认数据库路径是：

```text
~/.cc-switch/cc-switch.db
```

Windows 上通常是：

```text
C:\Users\<你的用户名>\.cc-switch\cc-switch.db
```

脚本会在数据库中读取当前启用的 Codex provider：

```text
providers where app_type = "codex" and is_current = 1
```

然后读取：

```text
key       -> providers.settings_config.auth.OPENAI_API_KEY
base_url  -> provider_endpoints.url
model     -> 默认使用 gpt-image-2
```

如果 ccswitch 中保存的 endpoint 是：

```text
https://relay.example
```

脚本会自动规范化为：

```text
https://relay.example/v1
```

### 检查 ccswitch

普通检查默认就会先尝试 ccswitch：

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check
```

也可以显式只检查 ccswitch：

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check-ccswitch
```

### 强制使用 ccswitch

正常情况下不需要写额外参数，因为脚本已经默认优先 ccswitch。

如果你希望“ccswitch 读取失败就直接报错，不要回退到 JSON 配置”，使用：

```powershell
python $skill generate --from-ccswitch --prompt-file prompts/test.txt --name test --force
```

### 跳过 ccswitch

如果你不想使用当前 ccswitch provider，而是想使用 JSON 配置：

```powershell
python $skill generate --no-ccswitch --prompt-file prompts/test.txt --name test --force
```

### 指定其他 ccswitch 数据库

```powershell
python $setup --check-ccswitch --ccswitch-db C:/path/to/cc-switch.db
python $skill generate --ccswitch-db C:/path/to/cc-switch.db --prompt-file prompts/test.txt --name test --force
```

## 使用 JSON 配置文件

如果你没有安装 ccswitch，或者图片中转站与 Codex 中转站不同，可以使用独立 JSON 配置。

### 推荐：用户级配置

运行：

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py config --scope user
```

Windows 下会写入：

```text
%APPDATA%\relay-imagegen\config.json
```

这种方式适合长期使用，而且不会把密钥放进 Skill 仓库中。

### Skill 私有配置

如果你只在本机自己使用，也可以写入 Skill 私有目录：

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py config --scope skill
```

文件位置为：

```text
~/.codex/skills/relay-imagegen/.secrets/config.json
```

`.secrets/` 已在仓库的 `.gitignore` 中忽略，不应上传到 GitHub。

### 手动创建 JSON

配置文件结构如下：

```json
{
  "api_key": "sk-...",
  "base_url": "https://relay.example/v1",
  "model": "gpt-image-2"
}
```

支持的 key 字段名：

```text
api_key, apiKey, key, token, openai_api_key, OPENAI_API_KEY
```

支持的 endpoint 字段名：

```text
base_url, baseUrl, baseURL, api_base, endpoint, openai_base_url, OPENAI_BASE_URL
```

## 生成图片

### 使用提示词文件生成 4K 图片

```powershell
$skill = "$HOME/.codex/skills/relay-imagegen/scripts/relay_imagegen.py"
python $skill generate `
  --prompt-file prompts/test.txt `
  --name test `
  --force
```

默认等价于：

```text
model   = gpt-image-2
size    = 3840x2160
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

长提示词、中文提示词或希望保留复用的提示词，建议使用 `--prompt-file`。

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

处理后的上传副本保存在：

```text
generated/relay_prepared/
```

原始图片不会被修改。

## 输出文件和运行记录

如果没有指定 `--out`，图片默认输出为：

```text
generated/<名称>-YYYYMMDD-HHMMSS-4k.png
```

例如：

```text
generated/character-chair-20260527-183000-4k.png
```

每次成功生成后，会自动写入同名记录文件：

```text
generated/character-chair-20260527-183000-4k.meta.json
```

记录内容包括：

- 使用的是 `generate` 还是 `edit`
- 模型
- 请求尺寸和输出尺寸
- 图片质量
- 提示词文件位置
- 输入参考图位置
- 预处理图片位置
- 耗时
- 配置来源
- 使用 ccswitch 时的 provider 名称
- 不含查询参数的 base URL

记录文件不会包含 API key。

## 常用参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `generate` / `edit` | 生成新图或使用参考图编辑 | 必填 |
| `--prompt-file` | 从文本文件读取提示词，推荐使用 | 无 |
| `--prompt` | 直接传入简短提示词 | 无 |
| `--image` | `edit` 模式的参考图，可重复传入 | 无 |
| `--name` | 自动输出文件名的名称部分 | 根据模式或 prompt 文件生成 |
| `--out` | 指定完整输出路径 | 自动命名 |
| `--output-dir` | 自动命名输出目录 | `generated` |
| `--size` | 输出分辨率 | `3840x2160` |
| `--quality` | 输出质量 | `high` |
| `--timeout` | 超时时间，单位为秒 | `600` |
| `--prepare-image` | 上传前压缩参考图 | 关闭 |
| `--max-input-edge` | 指定参考图最大边长，同时开启预处理 | `2048` |
| `--dry-run` | 只检查命令和输出位置，不实际请求 | 关闭 |
| `--force` | 允许覆盖目标输出 | 关闭 |
| `--config` | 显式指定 JSON 配置 | 自动读取 |
| `--from-ccswitch` | 强制使用 ccswitch，失败不回退 | 关闭 |
| `--no-ccswitch` | 不读取 ccswitch | 关闭 |
| `--ccswitch-db` | 指定 ccswitch 数据库位置 | `~/.cc-switch/cc-switch.db` |

## 安全说明

- 不要把真实 API key 作为命令行参数传入。
- 不要把真实配置文件提交到 GitHub。
- 不需要把 `OPENAI_API_KEY` 永久写入用户或系统环境变量。
- ccswitch 模式只读取当前 provider，不会复制 key 到 Skill 目录。
- JSON 配置模式只在运行时读取 key。
- key 只会临时注入子进程环境。
- 控制台只会显示脱敏后的 key。
- `.meta.json` 运行记录不会包含 key。

## 故障排查

### 没有找到配置

运行：

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py --check
```

如果没有安装或没有配置 ccswitch，创建用户级配置：

```powershell
python ~/.codex/skills/relay-imagegen/scripts/setup.py config --scope user
```

### 不想使用 ccswitch 当前 provider

运行时增加：

```powershell
--no-ccswitch
```

然后提供 JSON 配置，或者让脚本自动发现私有配置文件。

### ccswitch endpoint 不带 `/v1`

如果读取到的是根地址，例如：

```text
https://relay.example
```

脚本会自动转换为：

```text
https://relay.example/v1
```

如果你的中转站使用不同 API 路径，请改用 JSON 配置，明确写入完整 `base_url`。

### 输出不是 4K

默认请求尺寸是：

```text
3840x2160
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
  SKILL.md
  LICENSE
  agents/
    openai.yaml
  scripts/
    relay_imagegen.py
    setup.py
```

以下内容不会包含在公开仓库中：

```text
.secrets/config.json
generated/
*.meta.json
```

## License

[MIT License](LICENSE)
