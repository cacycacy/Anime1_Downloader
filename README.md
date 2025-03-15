# Anime1Downloader

Anime1Downloader 是一個基於 Python 的工具，用於從 [anime1.me](https://anime1.me) 下載動畫（支援 MP4 與 TS）。
支援單集影片連結與整季動畫連結，下載好的影片會根據動畫標題與標題中包含的季數分類，默認第一季。

## 需求

- Python 3.6+
- [ffmpeg](https://ffmpeg.org)（用於 TS 影片轉 mp4）

請使用以下指令安裝相依套件：

```bash
pip install -r requirements.txt
```

## 設定

可修改 `config.yml`來更改下載位置等設置：

```yaml
root_download_path: "./downloads"
urls_txt_path: "urls.txt"
use_multithreading: true
seasons:
  第一季: "01"
  第二季: "02"
  第三季: "03"
  第四季: "04"
  第五季: "05"
  第六季: "06"
  第七季: "07"
  第八季: "08"
  
headers:
  user-agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."
```

## 使用方法

1. 將要下載的 anime1.me 連結放入 `urls.txt`（或執行程式時直接輸入 URL，逗號分隔多個連結）。
2. 執行程式：

   ```bash
   python download_anime1.py
   ```

下載過程中會顯示進度條，下載完成後將顯示成功或失敗的情況。
