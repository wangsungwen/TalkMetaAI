# TalkMetaAI Hugging Face Space 零開始無痛部署手冊

本手冊說明如何從一台新電腦、GitHub Repo 與 Hugging Face 帳號開始，完整部署 TalkMetaAI 到 Hugging Face Space 雲端，並包含本專案部署時必須注意的程式碼修改、依賴修正、推送、Factory rebuild 與驗證流程。

適用目標：

- GitHub Repo: `https://github.com/wangsungwen/TalkMetaAI`
- Hugging Face Space: `https://huggingface.co/spaces/wangsongwen/TalkmetaAI`
- Space Host: `https://wangsongwen-talkmetaai.hf.space/`

參考文件：

- Hugging Face Docker Spaces: https://huggingface.co/docs/hub/spaces-sdks-docker
- Hugging Face Docker Space tutorial: https://huggingface.co/docs/hub/spaces-sdks-docker-first-demo
- Hugging Face Hub API `restart_space(factory_reboot=True)`: https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api

## 1. 整體部署觀念

Hugging Face Space 本質上也是一個 Git repo。部署可以有兩種方式：

1. 只推到 GitHub，讓 Hugging Face Space 自動同步 GitHub。
2. 直接推到 Hugging Face Space repo。

本專案建議採用「GitHub + Hugging Face Space 都同步」的方式：

- GitHub 保存主要原始碼與版本歷史。
- Hugging Face Space repo 保存實際雲端建置用的檔案。
- 每次修正部署問題後，先 push GitHub，再同步到 HF Space。

本專案使用 Docker Space，因此 Hugging Face 會讀取根目錄的 `Dockerfile` 建置映像檔。對外服務需要監聽：

```text
0.0.0.0:7860
```

Space 的 `README.md` YAML block 也需要指定：

```yaml
sdk: docker
app_port: 7860
```

## 2. 新電腦前置安裝

### 2.1 安裝 Git

下載並安裝 Git：

```text
https://git-scm.com/downloads
```

安裝後確認：

```powershell
git --version
```

### 2.2 安裝 Node.js

建議安裝 Node.js 20 LTS：

```text
https://nodejs.org/
```

確認：

```powershell
node --version
npm --version
```

### 2.3 啟用 pnpm

```powershell
corepack enable
corepack prepare pnpm@9.15.9 --activate
pnpm --version
```

### 2.4 安裝 Python 與 uv

本專案 server 使用 Python 3.10。

安裝 uv：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

確認：

```powershell
uv --version
```

### 2.5 安裝 Hugging Face CLI

如果本機尚未安裝 `hf`：

```powershell
pip install -U "huggingface_hub[cli]"
```

登入：

```powershell
hf auth login
```

確認登入帳號：

```powershell
hf auth whoami
```

## 3. 取得 GitHub Repo

選一個工作目錄：

```powershell
cd "C:\Users\你的使用者名稱\OneDrive\文件"
```

Clone repo：

```powershell
git clone https://github.com/wangsungwen/TalkMetaAI.git
cd TalkMetaAI
```

確認目前分支與遠端：

```powershell
git status --short --branch
git remote -v
```

如果需要設定 commit 身分：

```powershell
git config user.name "wangsungwen"
git config user.email "wangsungwen@users.noreply.github.com"
```

## 4. 確認 Hugging Face Space 設定

在 Hugging Face 建立 Space：

1. 進入 https://huggingface.co/new-space
2. Owner 選 `wangsongwen`
3. Space name 填 `TalkmetaAI`
4. SDK 選 `Docker`
5. Hardware 可先選 `CPU Basic`
6. Visibility 依需求選 Public 或 Private

Space 建立後，確認 Space repo 的 `README.md` 開頭包含：

```yaml
---
title: TalkMetaAI
sdk: docker
app_port: 7860
---
```

本專案使用 Dockerfile 裡的啟動命令：

```dockerfile
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"
```

這代表雲端會用 FastAPI/Uvicorn 對外提供服務。

## 5. 必要程式碼與依賴修正

### 5.1 修正 Kokoro 中文語音依賴

如果 Space log 出現：

```text
ModuleNotFoundError: No module named 'ordered_set'
```

原因是 Kokoro 的中文管線會載入 `misaki.zh`，它需要額外中文分詞、拼音與轉寫套件。

需要補上的依賴：

```text
jieba
g2pM
ordered-set
pypinyin
cn2an
```

注意：

- PyPI 套件名稱是 `ordered-set`
- Python import 模組名稱是 `ordered_set`

### 5.2 修改 Dockerfile

打開根目錄 `Dockerfile`，找到 `pip install` 區塊，確認包含以下套件：

```dockerfile
        "cn2an" \
        "g2pM" \
        "jieba" \
        kokoro \
        numpy \
        "ordered-set" \
        packaging \
        pypinyin \
```

完整概念是：Docker build 時必須安裝 Kokoro 中文管線需要的所有套件，否則本機可跑、Space 卻會在模型 preload 或 WebSocket 連線時失敗。

### 5.3 修改 apps/server/pyproject.toml

打開：

```text
apps/server/pyproject.toml
```

在 `[project]` 的 `dependencies` 裡確認包含：

```toml
    "kokoro",
    "jieba",
    "g2pM",
    "ordered-set",
    "pypinyin",
    "cn2an",
```

這樣本機 uv 環境與 Docker 環境才會一致。

### 5.4 更新 uv.lock

```powershell
cd apps/server
uv lock
cd ../..
```

成功時會看到新增類似：

```text
Added cn2an
Added g2pm
Added jieba
Added ordered-set
Added pypinyin
```

### 5.5 本機最小依賴驗證

不需要啟動大型模型，只測中文依賴 import：

```powershell
uvx --python 3.10 --with ordered-set --with jieba --with g2pM --with pypinyin --with cn2an --with numpy python -c "import ordered_set, jieba, g2pM, pypinyin, cn2an; print('zh deps ok')"
```

成功輸出：

```text
zh deps ok
```

## 6. 本機建置檢查

### 6.1 安裝前端依賴

```powershell
pnpm install
```

### 6.2 建置前端

```powershell
pnpm --filter @talkmateai/client build
```

成功後會產生：

```text
apps/client/out
```

Dockerfile 會把這個靜態輸出複製到後端映像中：

```dockerfile
COPY --from=client-build /app/apps/client/out apps/client/out
```

### 6.3 可選：本機 Docker build

如果本機有 Docker Desktop：

```powershell
docker build -t talkmetaai .
docker run --rm -p 7860:7860 talkmetaai
```

打開：

```text
http://127.0.0.1:7860/
```

如果本機沒有 Docker，也可以跳過，直接讓 Hugging Face Space build。

## 7. 提交到 GitHub

確認修改：

```powershell
git status --short
```

加入三個關鍵檔案：

```powershell
git add Dockerfile apps/server/pyproject.toml apps/server/uv.lock
```

提交：

```powershell
git commit -m "Fix Hugging Face Kokoro Chinese dependencies"
```

推到 GitHub：

```powershell
git push origin main
```

確認：

```powershell
git log --oneline -3
```

## 8. 同步到 Hugging Face Space

### 8.1 方法 A：用 git remote 推到 Space

加入 HF remote：

```powershell
git remote add hf https://huggingface.co/spaces/wangsongwen/TalkmetaAI
```

如果已經加過會出現錯誤，可以忽略，或先確認：

```powershell
git remote -v
```

推送到 Space：

```powershell
git push hf main:main
```

### 8.2 方法 B：git push 卡住時，用 Hugging Face SDK 直接提交

有時 Windows 環境的 git 認證可能卡住。可以改用 SDK 只提交部署需要的檔案：

```powershell
python -c "from pathlib import Path; from huggingface_hub import HfApi, CommitOperationAdd; root=Path.cwd(); repo_id='wangsongwen/TalkmetaAI'; paths=['Dockerfile','apps/server/pyproject.toml','apps/server/uv.lock']; ops=[CommitOperationAdd(path_in_repo=p, path_or_fileobj=str(root / p)) for p in paths]; print(HfApi().create_commit(repo_id=repo_id, repo_type='space', operations=ops, commit_message='Fix Kokoro Chinese dependencies'))"
```

確認 HF Space HEAD：

```powershell
git ls-remote https://huggingface.co/spaces/wangsongwen/TalkmetaAI HEAD refs/heads/main
```

## 9. 執行 Factory Rebuild

普通 restart 可能沿用快取。若你剛修了 Dockerfile 或 Python 依賴，建議執行 Factory rebuild。

使用 Hugging Face SDK：

```powershell
python -c "from huggingface_hub import HfApi; print(HfApi().restart_space(repo_id='wangsongwen/TalkmetaAI', factory_reboot=True))"
```

狀態通常會依序變成：

```text
RUNNING_BUILDING
RUNNING_APP_STARTING
RUNNING
```

## 10. 監控部署狀態

查看 Space info：

```powershell
hf spaces info wangsongwen/TalkmetaAI
```

查看 runtime logs：

```powershell
hf spaces logs wangsongwen/TalkmetaAI --tail 200
```

查看 build logs：

```powershell
hf spaces logs wangsongwen/TalkmetaAI --build --tail 120
```

如果 Windows PowerShell 出現 CP950 編碼錯誤，先切 UTF-8：

```powershell
chcp 65001
$env:PYTHONIOENCODING="utf-8"
```

再重新執行 logs 指令。

## 11. 驗證雲端服務

### 11.1 網頁驗證

打開：

```text
https://wangsongwen-talkmetaai.hf.space/
```

### 11.2 HTTP 驗證

```powershell
(Invoke-WebRequest -Uri "https://wangsongwen-talkmetaai.hf.space/" -UseBasicParsing -TimeoutSec 30).StatusCode
```

成功應回：

```text
200
```

### 11.3 Log 成功訊號

正常 log 應看到：

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:7860
WebSocket /ws/test-client [accepted]
```

且不應再看到：

```text
ModuleNotFoundError: No module named 'ordered_set'
```

## 12. 手機連線測試

手機直接打開：

```text
https://wangsongwen-talkmetaai.hf.space/
```

確認：

- 頁面可載入。
- Avatar 可顯示。
- WebSocket 可連線。
- 麥克風權限允許後可互動。
- Logs 沒有新的 Python exception。

手機測試請使用 HTTPS Space URL，不要使用：

```text
http://0.0.0.0:7860/
http://127.0.0.1:7860/
```

這兩個只適合本機容器或本機開發測試。

## 13. 常見問題排除

### 13.1 `ModuleNotFoundError: ordered_set`

原因：

```text
Kokoro -> misaki.zh -> transcription.py -> ordered_set
```

解法：

- `Dockerfile` 加入 `ordered-set`
- `apps/server/pyproject.toml` 加入 `ordered-set`
- 同時補上 `jieba`、`g2pM`、`pypinyin`、`cn2an`
- 重新 `uv lock`
- push 到 GitHub 與 HF Space
- 執行 Factory rebuild

### 13.2 Space rebuild 後還是舊錯誤

可能原因：

- 只 push 到 GitHub，沒有同步到 HF Space repo。
- Space build 快取還在。
- Space runtime 還跑舊 sha。

檢查：

```powershell
git ls-remote https://huggingface.co/spaces/wangsongwen/TalkmetaAI HEAD refs/heads/main
hf spaces info wangsongwen/TalkmetaAI
```

解法：

```powershell
python -c "from huggingface_hub import HfApi; print(HfApi().restart_space(repo_id='wangsongwen/TalkmetaAI', factory_reboot=True))"
```

### 13.3 Build log 看不到或 Windows 編碼錯誤

先切 UTF-8：

```powershell
chcp 65001
$env:PYTHONIOENCODING="utf-8"
```

再查：

```powershell
hf spaces logs wangsongwen/TalkmetaAI --build --tail 120
```

### 13.4 CPU Basic 很慢

CPU Basic 可以啟動，但 AI 模型推理會慢。若需要實用速度，建議改 GPU Space。

查看硬體：

```powershell
hf spaces hardware
```

硬體升級會產生費用，調整前請先確認 Hugging Face billing。

### 13.5 WebSocket 連線失敗

確認服務是雲端 HTTPS：

```text
https://wangsongwen-talkmetaai.hf.space/
```

確認 server log 有：

```text
WebSocket /ws/test-client [accepted]
```

如果前端仍連到 localhost 或 0.0.0.0，需要檢查前端 WebSocket URL 邏輯。

## 14. 一鍵部署檢查清單

每次修改後照這份 checklist：

```text
[ ] Dockerfile 已包含中文 TTS 依賴
[ ] apps/server/pyproject.toml 已包含中文 TTS 依賴
[ ] apps/server/uv.lock 已更新
[ ] 本機 import 測試通過
[ ] git status 確認只有預期檔案
[ ] commit 到 GitHub
[ ] push 到 GitHub main
[ ] 同步到 Hugging Face Space repo
[ ] 執行 Factory rebuild
[ ] Space stage 變成 RUNNING
[ ] 首頁 HTTP status 200
[ ] Logs 沒有 ordered_set 錯誤
[ ] 手機 HTTPS 測試正常
```

## 15. 本次成功部署紀錄

本次修正內容：

- `Dockerfile` 補上 Kokoro 中文管線依賴
- `apps/server/pyproject.toml` 補上同一批依賴
- `apps/server/uv.lock` 重新解析
- GitHub push 完成
- Hugging Face Space repo 同步完成
- Factory rebuild 完成
- Space 狀態回到 `RUNNING`
- 手機連網測試正常

成功判斷：

```text
HTTP 200
Application startup complete
WebSocket accepted
No ModuleNotFoundError: ordered_set
```
