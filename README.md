# bpc-cli

基于系统 7-Zip 的压缩/解压命令行工具，支持文件后缀名混淆（防和谐）与嵌套压缩包递归解压。

## 安装依赖

- Python >= 3.14
- 安装 [7-Zip](https://www.7-zip.org/) 并确保 `7z` 在系统 PATH 中，或通过 `--sevenz` / 环境变量 `SEVENZ_PATH` 指定。

## 安装

```bash
uv pip install -e .
```

## 用法

`bpc` 是一个命令组，提供 `c` / `e` 两个简写子命令，同时保留 `compress` / `extract` 旧名以兼容已有脚本。

### 压缩

```bash
# 默认递归压缩 3~4 层，输出与输入同级
bpc c src_dir

# 指定输出文件
bpc c src_dir -o packed.7z

# 单层压缩
bpc c src_dir -s

# 旧子命令名仍可用
bpc compress src_dir -o packed.7z
```

- `-o, --output`：输出压缩包路径（默认与输入同级，文件名同输入）
- `-p, --password`：压缩密码；无需密码可直接回车
- `--recursive/--no-recursive`：是否递归压缩子目录（单层压缩时，默认开启）
- `-s, --single`：仅单层压缩，禁用默认递归压缩
- `--volume-size`：分卷大小（递归压缩未指定时默认 1G）
- `--recursive-levels`：显式指定递归压缩层数（2~10）
- `--sevenz`：指定 7z 可执行文件路径
- `-v, --verbose`：输出详细日志

压缩完成后，原始文件扩展名会被随机 4 位字符替换，真实扩展名映射加密保存在 `.bpc_extmap` 中。

### 解压

```bash
bpc e packed.7z -o out_dir
bpc e src_dir -o out_dir -p
```

- `-o, --output`：解压输出根目录（默认当前目录）
- `-p, --password`：解压密码
- `--recursive/--no-recursive`：是否递归扫描输入目录中的压缩包（默认开启）
- `--max-depth`：嵌套压缩包最大递归深度（默认 10）
- `-j, --jobs`：并行解压线程数（默认按 CPU 核心自动选择，最多 4）
- `--sevenz`：指定 7z 可执行文件路径
- `-v, --verbose`：输出详细日志

解压时会自动识别并恢复被混淆的文件后缀名。

### 支持的压缩格式

底层依赖 7-Zip，因此兼容：`.7z`、`.zip`、`.rar`、`.tar`、`.gz`、`.bz2`、`.xz`、`.tar.gz`、`.tar.bz2`、`.tar.xz`、`.iso`、`.cab` 等。

## 示例：压缩后再解压

```bash
# 压缩（默认递归多层）
bpc c my_folder -p mypassword

# 解压
bpc e my_folder.7z -o restored -p mypassword
```

## 测试

```bash
uv run pytest tests/ -v
```

## 命令入口

- `bpc`：统一命令组入口
- `bpc c` / `bpc compress`：压缩
- `bpc e` / `bpc extract`：解压
- 旧命令 `bpc-extract-click` 已移除
