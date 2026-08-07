# 使用教程

<div align="center">
  <a href="../README.md">项目首页</a> ·
  <a href="./README.md">文档中心</a> ·
  <a href="./config.md">配置参数</a> ·
  中文 | <a href="./tutorial_en.md">English</a>
</div>

> [!TIP]
> 项目支持工作流、命令行、GUI 和 Docker 共 4 种运行方式，请按使用环境选择。

<details open>
<summary><strong>目录</strong></summary>

- [工作流部署](#工作流部署)
- [命令行](#命令行)
- [GUI 软件](#gui-软件)
- [Docker](#docker)
  - [推流使用教程](#推流使用教程)

</details>

## 工作流部署

使用 GitHub Actions 部署并手动执行更新。

> [!IMPORTANT]
> GitHub Actions 资源有限，工作流更新只能手动触发。如果需要频繁更新或定时执行，请使用其他方式部署。

### 进入IPTV-API项目

打开 https://github.com/Guovin/iptv-api 点击`Star`收藏该项目（您的Star是我持续更新的动力）
![Star](./images/star.png 'Star')

### Fork

将本仓库的源代码复制至个人账号仓库中
![Fork入口](./images/fork-btn.png 'Fork入口')

1. 个人仓库命名，可按您喜欢的名字随意命名（最终直播源结果链接取决于该名称），这里以默认`iptv-api`为例
2. 确认信息无误后，点击确认创建

![Fork详情](./images/fork-detail.png 'Fork详情')

### 更新源代码

由于本项目将持续迭代优化，如果您想获取最新的更新内容，可进行如下操作

> [!WARNING]
> 如果您的目的是更新自己 Fork 的代码，请不要点击 `Contribute` 或 `Open pull request` 创建 PR。
> 请进入您自己的仓库主页，使用 `Sync fork` → `Update branch`。
> 如果出现同步冲突，请按照下方说明选择 `Discard commits`。
> 只有想向主仓库贡献代码时，才需要创建 Pull Request。

#### 1. Watch

关注该项目，后续更新日志将以`releases`发布，届时您将收到邮件通知
![Watch-activity](./images/watch-activity.png 'Watch All Activity')

#### 2. Sync fork

- 正常更新：

回到您 Fork 后的仓库首页，如果项目有更新内容，点击`Sync fork`，`Update branch`确认即可更新最新代码
![Sync-fork](./images/sync-fork.png 'Sync fork')

- 没有`Update branch`按钮，更新冲突：

这是因为某些文件与主仓库的默认文件冲突了，点击`Discard commits`即可更新最新代码
![冲突解决](./images/conflict.png '冲突解决')

> [!IMPORTANT]
> 为了避免后续更新代码发生冲突，以下修改`config`目录下的文件时建议复制文件后重命名添加`user_`前缀

### 修改模板

当您在步骤一中点击确认创建，成功后会自动跳转到您的个人仓库。这个时候您的个人仓库就创建完成了，可以定制个人的直播源频道菜单了！

#### 1. 点击 config 文件夹内 demo.txt 模板文件：

![config文件夹入口](./images/config-folder.png 'config文件夹入口')

![demo.txt入口](./images/demo-btn.png 'demo.txt入口')

您可以复制并参考默认模板的格式进行后续操作。

#### 2. config 文件夹内创建个人模板 user_demo.txt：

1. 点击`config`目录
2. 创建文件
3. 模板文件命名为`user_demo.txt`
4. 模板文件需要按照（频道分类,#genre#），（频道名称,频道接口）进行编写，注意是英文逗号。如果需要将该接口设为白名单（不测速、保留在结果最前），可在地址后添加
   `$!`即可，例如http://xxx$!。后面也可以添加额外说明信息，如：http://xxx$!白名单
5. 点击`Commit changes...`进行保存

![创建user_demo.txt](./images/edit-user-demo.png '创建user_demo.txt')

### 修改配置

跟编辑模板一样，修改运行配置

#### 1. 点击 config 文件夹内的 config.ini 配置文件：

![config.ini入口](./images/config-btn.png 'config.ini入口')

#### 2. 复制默认配置文件内容：

![copy config.ini](./images/copy-config.png '复制默认配置')

#### 3. config 文件夹内新建个人配置文件 user_config.ini：

1. 创建文件
2. 配置文件命名为`user_config.ini`
3. 粘贴默认配置 （创建`user_config.ini`可以只输入想要修改的配置项即可，无需全部复制 config.ini，注意配置文件上方的
   `[Settings]`必须保留，否则下方的自定义配置不生效）
4. 修改模板和结果文件配置以及CDN代理加速（推荐）：
    - source_file = config/user_demo.txt
    - final_file = output/user_result.txt
    - cdn_url = （前往`Govin`公众号回复`cdn`获取）
5. 点击`Commit changes...`进行保存

![创建user_config.ini](./images/edit-user-config.png '创建user_config.ini')
![编辑final_file配置](./images/edit-user-final-file.png '编辑source_file配置')
![编辑source_file配置](./images/edit-user-source-file.png '编辑source_file配置')

按照您的需要适当调整配置，以下是默认配置说明：
[配置参数](./config.md)

> [!NOTE]
> 1. 部分播放器（如 `PotPlayer`）不支持解析接口补充信息，可能导致无法播放。可设置 `open_url_info = False`（GUI：取消勾选“显示接口信息”）关闭该功能
> 2. 如果网络确定支持 IPv6，可设置 `ipv6_support = True`（GUI：勾选“强制认为当前网络支持 IPv6”）跳过支持性检查
> 3. 如需为接口指定播放/测速请求头，可配置全局 `user_agent`（统一 UA），或在 `config/subscribe.txt` 的订阅地址后追加 `UA=值`（针对单个订阅源）；UA 会写入 `.m3u` 结果，无需开启 `open_headers`
> 4. 配置 `location`（归属地）/`isp`（运营商）后，默认会直接过滤掉不匹配的接口；若开启 `open_supply = True`，不匹配的接口将不再丢弃，而是降权排到该频道结果末尾作为补充，避免可用接口被误删
> 5. 通过 `sort_by` 自定义每个频道内接口的排序优先级，逗号分隔，可选 `speed`（速率）、`delay`（延迟）、`resolution`（分辨率），按从前到后依次比较，例如 `resolution,speed` 表示优先按分辨率、其次按速率排序

#### 添加数据源与更多

- 订阅源（`config/subscribe.txt`）

  由于没有提供默认订阅地址，所以您需要自行添加，否则更新结果可能为空。支持txt和m3u地址作为订阅，程序将依次读取其中的频道接口数据。
  ![订阅源](./images/subscribe.png '订阅源')

  如果某个订阅源需要特定的 `User-Agent` 才能访问，可在订阅地址后追加 `UA=值` 指定（包含空格时用引号包裹），例如：

  ```text
  https://example.com/sub.m3u UA=okHttp/Mod-1.5.0.0
  https://example.com/sub2.m3u UA="Mozilla/5.0 xxx"
  ```

  该 `UA` 会同时用于：拉取该订阅内容、对该订阅源下各接口测速、以及写入 `.m3u` 结果（供播放器使用），无需开启 `open_headers`。若希望对所有接口统一指定一个 UA（避免逐条添加），可在配置中设置全局 `user_agent`。优先级：接口自带 UA（m3u 内含 `#EXTVLCOPT`）> 订阅地址 UA > 全局 `user_agent` > 内置默认 UA。注意：请求头只能写入 `.m3u` 结果，`.txt` 格式无法携带 UA。


- 本地源（`config/local.txt`）

  频道接口数据来源于本地文件，如果有多个本地源文件，可以在`config`下创建`local`目录进行存放，程序将依次读取其中的频道接口数据，支持txt/m3u文件。


- 台标源（`config/logo`）

  频道台标图片存放目录，程序会根据模板中的频道名称去该目录下匹配对应的台标图片，如果使用了远程库`logo_url`，则优先从远程库中获取


- EPG源（`config/epg.txt`）

  频道预告信息数据来源，程序将依次获取文件中订阅地址的频道预告数据，进行汇总输出


- 频道别名（`config/alias.txt`）

  频道名称的别名名单，用于获取接口时将多种名称映射为一个名称的结果，可以提升获取量与准确率，格式：模板频道名称,别名1,别名2,别名3


- 黑名单（`config/blacklist.txt`）

  符合黑名单关键字的接口将会被过滤，不会被收集，比如含广告等低质量接口


- 白名单（`config/whitelist.txt`）

  白名单内的接口或订阅源获取的接口将不会参与测速，优先排序至结果最前。填写频道名称会直接保留该记录至最终结果，如：CCTV-1,接口地址，只填写接口地址则对所有频道生效，多条记录换行输入。

> [!TIP]
> 如果运行后没有频道数据，日志会区分“未配置数据源、订阅请求无数据、频道未匹配、结果全部被过滤”等原因。GUI 可从首页进入“配置数据源”；Docker 用户应确认容器内 `/iptv-api/config/subscribe.txt` 不是空文件。

### 运行更新

如果您的模板和配置修改没有问题的话，这时就可以配置`Actions`来实现自动更新

#### 1. 进入 Actions：

![Actions入口](./images/actions-btn.png 'Actions入口')

#### 2. 开启 Actions 工作流：

![开启Actions工作流](./images/actions-enable.png '开启Actions工作流')
由于 Fork 的仓库 Actions 工作流是默认关闭的，需要您手动确认开启，点击红框中的按钮确认开启

![Actions工作流开启成功](./images/actions-home.png 'Actions工作流开启成功')
开启成功后，可以看到目前是没有任何工作流在运行的，别急，下面开始运行您第一个更新工作流

#### 3. 运行更新工作流：

##### （1）启用update schedule：

1. 点击`Workflows`分类下的`update schedule`
2. 由于 Fork 的仓库工作流是默认关闭的，点击`Enable workflow`按钮确认开启

![开启Workflows更新](./images/workflows-btn.png '开启Workflows更新')

##### （2）根据分支运行 Workflow：

这个时候就可以运行更新工作流了

1. 点击`Run workflow`
2. 这里可以切换您要运行的仓库分支，由于 Fork 默认拉取的是`master`分支，如果您修改的模板和配置也在该分支，这里选择`master`
   就好了，点击`Run workflow`确认运行

![运行Workflow](./images/workflows-run.png '运行Workflow')

##### （3）Workflow 运行中：

稍等片刻，就可以看到您的第一条更新工作流已经在运行了！

![Workflow运行中](./images/workflow-running.png 'Workflow运行中')

> [!NOTE]\
> 由于运行时间取决于您的模板频道数量以及页数等配置，也很大程度取决于当前网络状况，请耐心等待，默认模板与配置一般需要15
> 分钟左右。

##### （4）Workflow 取消运行：

如果您觉得这次的更新不太合适，需要修改模板或配置再运行，可以点击`Cancel run`取消本次运行
![取消运行Workflow](./images/workflow-cancel.png '取消运行Workflow')

##### （5）Workflow 运行成功：

如果一切正常，稍等片刻后就可以看到该条工作流已经执行成功（绿色勾图标）

![Workflow执行成功](./images/workflow-success.png 'Workflow执行成功')

此时您可以访问文件链接，查看最新结果有没有同步即可：
https://raw.githubusercontent.com/您的github用户名/仓库名称（对应上述Fork创建时的iptv-api）/master/output/user_result.txt

代理加速地址（推荐）：
{cdn_url}/https://raw.githubusercontent.com/您的github用户名/仓库名称（对应上述Fork创建时的iptv-api）/master/output/user_result.txt

![用户名与仓库名称](./images/rep-info.png '用户名与仓库名称')

如果访问该链接能正常返回更新后的接口内容，说明您的直播源接口链接已经大功告成了！将该链接复制粘贴到`TVBox`
等播放器配置栏中即可使用~

> [!NOTE]\
> 如果您修改了模板或配置文件想立刻执行更新，可手动触发（2）中的`Run workflow`即可。

## 命令行

1. 安装 Python
   请至官方下载并安装 Python，安装时请选择将 Python 添加到系统环境变量 Path 中

2. 运行更新
   项目目录下打开终端 CMD 依次运行以下命令：

安装依赖：

```shell
pip install pipenv
```

```shell
pipenv install --dev
```

启动更新：

```shell
pipenv run dev
```

启动服务：

```shell
pipenv run service
```

## GUI 软件

新版桌面 GUI 面向 Windows 与 macOS，提供一键更新、实时进度、频道与结果管理、重新测速、RTMP 推流监控、数据源配置及任务历史。Docker 部署使用 Web 结果页，不包含此桌面界面。

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./images/desktop-ui-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="./images/desktop-ui.png">
    <img src="./images/desktop-ui.png" alt="IPTV-API 新版桌面端界面" width="100%"/>
  </picture>
  <details>
    <summary>🌓 切换显示模式</summary>
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="./images/desktop-ui.png">
      <source media="(prefers-color-scheme: light)" srcset="./images/desktop-ui-dark.png">
      <img src="./images/desktop-ui-dark.png" alt="IPTV-API 新版桌面端另一主题界面" width="100%"/>
    </picture>
  </details>
</div>

在项目目录安装依赖并启动：

```shell
pipenv install --dev
pipenv run ui
```

构建当前系统的桌面应用：

```shell
pipenv run ui_build
```

配置保存到 `config/user_config.ini`，运行结果、频道快照、任务历史与日志保存在 `output/`。打包应用首次启动时，这两个目录位于系统应用数据目录。启用分辨率检测前请安装 FFmpeg。Windows 包内可附带 nginx-rtmp；macOS 需要安装带 RTMP 模块的 nginx，桌面端会自动生成独立配置并启动，也可通过 `IPTV_API_NGINX_PATH` 和 `IPTV_API_NGINX_RTMP_MODULE` 指定路径。

旧版 Tkinter 界面已弃用，仅为兼容现有用户而临时保留，并将在后续版本中移除。该界面不再维护、修复问题或新增功能；过渡期间仍可通过 `pipenv run legacy_ui` 启动，并通过 `pipenv run legacy_ui_build` 打包。

## Docker

### 1. Compose 部署（推荐）

下载[docker-compose.yml](../docker-compose.yml)或复制内容创建（内部参数可按需更改），在文件所在路径下运行以下命令即可部署：

```bash
docker compose up -d
```

### 2. 手动命令部署

#### （1）拉取镜像

```bash
docker pull guovern/iptv-api:latest
```

🚀 代理加速（若拉取失败可以使用该命令，但有可能拉取的是旧版本）：

```bash
docker pull docker.1ms.run/guovern/iptv-api:latest
```

#### （2）运行容器

```bash
docker run -d -p 80:8080 guovern/iptv-api
```

**环境变量：**

| 变量              | 描述                                                | 默认值       |
|:----------------|:--------------------------------------------------|:----------|
| PUBLIC_URL      | 推荐：完整公网地址，例如 `http://192.168.1.10` 或 `https://iptv.example.com` |           |
| PUBLIC_DOMAIN   | 兼容配置：`PUBLIC_URL` 留空时使用的公网域名或 IP                    | 127.0.0.1 |
| PUBLIC_PORT     | 兼容配置：`PUBLIC_URL` 留空时使用的宿主机映射端口                    | 80        |
| NGINX_HTTP_PORT | 高级兼容配置：容器内部 HTTP 端口，通常保持默认                        | 8080      |

> 当宿主机/Docker 已启用 IPv6 时，容器会自动同时监听 IPv6 地址，无需额外配置；纯 IPv4 或禁用 IPv6 的环境则自动跳过。

如果需要修改环境变量，在上述运行命令后添加以下参数：

```bash
# 推荐：直接设置完整公网地址
-e PUBLIC_URL=https://iptv.example.com
```

使用仓库中的 Compose 文件时，只需通过 `PORT` 修改宿主机端口，例如
`PORT=8088 docker compose up -d`。
未设置或留空的 `PUBLIC_URL` 不会覆盖挂载配置文件中的 `public_url`。

除了以上环境变量，还支持通过环境变量覆盖配置文件中的[配置项](../docs/config.md)

**挂载：** 实现宿主机文件与容器文件同步，修改模板、配置、获取更新结果文件可直接在宿主机文件夹下操作，在上述运行命令后添加以下参数

```bash
# 挂载配置目录
-v /iptv-api/config:/iptv-api/config
# 挂载结果目录
-v /iptv-api/output:/iptv-api/output
```

#### 3. 更新结果

| 接口              | 描述          |
|:----------------|:------------|
| /               | 默认接口        |
| /m3u            | m3u 格式接口    |
| /txt            | txt 格式接口    |
| /ipv4           | ipv4 默认接口   |
| /ipv6           | ipv6 默认接口   |
| /ipv4/txt       | ipv4 txt接口  |
| /ipv6/txt       | ipv6 txt接口  |
| /ipv4/m3u       | ipv4 m3u接口  |
| /ipv6/m3u       | ipv6 m3u接口  |
| /content        | 接口文本内容      |
| /log/result     | 有效结果的日志     |
| /log/speed-test | 所有参与测速接口的日志 |
| /log/statistic  | 统计结果的日志     |
| /log/unmatch    | 未匹配频道的日志    |

**RTMP 推流：**

> [!NOTE]
> 1. 如果是服务器部署，建议通过 `PUBLIC_URL` 配置完整公网地址；旧版 `PUBLIC_DOMAIN` 与 `PUBLIC_PORT` 仍兼容
> 2. 开启推流后，默认会将获取到的接口（如订阅源）进行推流；请仅对你有明确授权、可合法分发或仅用于内部测试的内容启用该功能
> 3. 如果需要对本地视频源进行推流，可在`config`目录下新建`hls`文件夹，将以`频道名称命名`的视频文件放入其中，程序会自动推流到对应的频道中
> 4. 在中国大陆使用时，请特别确认内容授权、版权、网络视听与广播电视等相关合规要求；不要将本项目用于传播、转发或公开分发未经授权的直播源/节目源

| 推流接口          | 描述           |
|:--------------|:-------------|
| /hls          | 推流接口         |
| /hls/txt      | 推流txt接口      |
| /hls/m3u      | 推流m3u接口      |
| /hls/ipv4     | 推流ipv4 默认接口  |
| /hls/ipv6     | 推流ipv6 默认接口  |
| /hls/ipv4/txt | 推流ipv4 txt接口 |
| /hls/ipv4/m3u | 推流ipv4 m3u接口 |
| /hls/ipv6/txt | 推流ipv6 txt接口 |
| /hls/ipv6/m3u | 推流ipv6 m3u接口 |
| /stat         | 推流状态统计接口     |

### 推流使用教程

Docker 中启用推流很简单——只需做少量配置并将需要推流的频道或视频放到指定位置，程序会自动将这些源通过内置 RTMP/HTTP 推出为可播放的
HLS 流。请仅用于你有明确授权的内容、个人自有内容或封闭环境的技术测试，不要用于未经授权的公开转播。

下面以两种常见方式说明：订阅源推流（在线源）和本地视频推流（上传视频文件）。

#### 1. 启动前准备（以 Docker Compose 部署为例）

- 使用本仓库提供的 `docker-compose.yml`，确认并根据需要修改下面的环境变量：
    - `PORT`：映射到宿主机的访问端口。
    - `PUBLIC_URL`：推荐填写完整公网地址，用于生成推流和播放列表链接。
    - `NGINX_HTTP_PORT`：高级兼容项，容器内部 HTTP 端口通常保持默认。
- 确保将配置目录挂载到容器内（默认：`/iptv-api/config`），便于在宿主机上修改模板、放置本地视频等。

示例（摘自 compose 配置，保留用于参考）：

```yml
services:
  iptv-api:
    image: guovern/iptv-api:latest
    container_name: iptv-api
    restart: unless-stopped

    ports:
      - "${PORT:-80}:8080" # PORT 是用户访问端口；8080 是固定的容器内部端口

    volumes:
      - /iptv-api/config:/iptv-api/config # 修改为宿主机配置文件夹路径:容器内配置文件夹路径
      - /iptv-api/output:/iptv-api/output

    environment:
      PUBLIC_URL: "${PUBLIC_URL:-http://192.168.1.95}" # 修改为完整公网地址
      PUBLIC_PORT: "${PORT:-80}" # 兼容旧配置，由 PORT 自动同步
      NGINX_HTTP_PORT: "8080" # 高级兼容项，通常不要修改
      CDN_URL: ""
      HTTP_PROXY: ""
```

#### 2. 订阅源推流（在线源）

- 在 `config/subscribe.txt` 中添加订阅地址（支持 txt 和 m3u）。启动后，程序会读取订阅并对其中的频道进行推流。
- 推流接口示例：访问 `/hls/txt`、`/hls/m3u` 或带 ipv4/ipv6 前缀的接口以查看当前推流的频道列表。

#### 3. 本地视频推流（服务器上的视频文件）

- 在挂载的 `config` 目录下创建 `hls` 文件夹（若使用容器挂载为 `/iptv-api/config/hls`），将需要推流的本地视频文件放入，文件名应与模板里的频道名称对应。

例如：

```
iptv-api/
├── config
│   └── hls
│       └── 海洋.mp4
```

- 在 `config/demo.txt` 中添加对应频道条目，程序会将该本地文件当成该频道的推流源。

示例模板片段：

```markdown
📺央视频道,#genre#
CCTV-1

📡卫视频道,#genre#
广东卫视

🚀本地视频,#genre#
海洋
```

#### 4. 启动并验证

- 启动容器：

```bash
docker compose up -d
```

- 启动成功后可通过以下页面/接口确认：
    - 启动日志：
      ![Finish-Log](./images/finish-log.png 'Finish Log')

    - 推流结果（txt 格式示例）：
      访问 `/hls/txt` 可查看当前推流的地址及说明：
      ![Hls-Txt-Result](./images/hls-txt-result.png 'Hls Txt Result')

    - 浏览器播放示例（订阅源与本地视频）：
      ![Hls-Web-Subscribe](./images/hls-web-subscribe.png 'Hls Web Subscribe')
      ![Hls-Web-Local](./images/hls-web-local.png 'Hls Web Local')

    - 在播放器中加载完整频道菜单（示例使用 PotPlayer）：
      ![PotPlayer](./images/potplayer.png 'PotPlayer')

#### 5. 监控与日志

- 推流状态统计页面 `/stat` 用于查看当前推流数、流量等：
  ![Rtmp-Stat](./images/rtmp-stat.png 'Rtmp Stat')

- 也可以查看容器日志来观察频道开始/停止推流的详细记录：
    - 频道开始推流：
      ![Hls-Start-Log](./images/hls-start-log.png 'Hls Start Log')
    - 频道空闲无人观看自动停止：
      ![hls-will-Stop-Log](./images/hls-will-stop-log.png 'Rtmp Will Stop Log')
      ![hls-Stop-Log](./images/hls-stop-log.png 'Rtmp Stop Log')

#### 6. 常见提示与调优建议

- 公网访问与防火墙：确保 `PUBLIC_URL` 中的 HTTP 端口和 RTMP 端口已对外放通（防火墙、云服务安全组等）。
- 域名与证书：若使用域名并启用 HTTPS，请直接将 `PUBLIC_URL` 设置为 `https://你的域名`，并在外部配置好反向代理或证书。
- 性能与并发：本地推流会消耗 CPU 和带宽，建议合理设置 `rtmp_max_streams` 限制并发推流数量，避免服务器过载。
- 空闲停止：`rtmp_idle_timeout` 控制无人观看后自动停止推流的超时时间（秒），可根据服务器资源与使用场景调整。

#### 7. 推流常用相关配置项

```ini
# RTMP 频道接口空闲停止推流超时时长（秒）
rtmp_idle_timeout = 300
# RTMP 推流最大并发数量，避免过高导致服务器压力过大
rtmp_max_streams = 10
```

以上是简洁的推流使用说明。按需调整配置并通过 `/hls/*` 与 `/stat` 等接口验证推流状态和可用性即可。
