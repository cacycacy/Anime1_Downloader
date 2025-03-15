#!/usr/bin/python
# -*- coding: UTF-8 -*-
import re
import sys
import time
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import threading
import concurrent.futures
import urllib.parse

import requests
from bs4 import BeautifulSoup
import m3u8
import yaml
import colorama
from colorama import Fore, Style

from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)

colorama.init()

# 全域輸出鎖（僅用於同步列印訊息）
print_lock = threading.Lock()


class AnimeDownloader:
    def __init__(self, config_path: str = 'config.yml'):
        """
        初始化設定與建立下載目錄
        """
        self.config: dict = self.load_config(config_path)
        self.root_download_path: Path = Path(self.config['root_download_path'])
        self.urls_path: str = self.config['urls_path']
        self.use_multithreading: bool = self.config.get('use_multithreading', False)
        self.seasons: dict = self.config['seasons']
        self.HEADERS: dict = self.config['headers']
        self.failed_list: List[str] = []
        self.session: requests.Session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.create_download_directory()
        # 全域 Progress 物件，下載時會使用
        self.progress: Optional[Progress] = None

    @staticmethod
    def load_config(config_path: str) -> dict:
        """從 YAML 配置檔讀取設定"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    @staticmethod
    def styled_print(message: str, color: str = Fore.WHITE, style: str = Style.NORMAL,
                     prefix: str = "", suffix: str = "") -> None:
        """統一格式化列印訊息"""
        with print_lock:
            print(f"{prefix}{color}{style}{message}{Style.RESET_ALL}{suffix}")

    @staticmethod
    def sanitize_video_name(video_name: str) -> str:
        """移除檔名中非法字元"""
        return re.sub(r'[\\/:*?"<>|]', '', video_name)

    @staticmethod
    def parse_video_name(video_name: str) -> Tuple[str, str]:
        """
        解析影片名稱，格式必須為 '動畫名稱[集數]'
        """
        match = re.search(r"(.*)\[(.*)\]", video_name)
        if not match:
            raise ValueError("影片名稱格式不正確，應為 '動畫名稱[集數]'")
        return match.group(1).strip(), match.group(2).strip()

    def get_and_replace_season_number(self, anime_name: str) -> Tuple[Optional[str], str]:
        """
        檢查並移除動畫名稱中的季節資訊，返回季號與更新後的名稱
        """
        for season_chinese, season_code in self.seasons.items():
            if season_chinese in anime_name:
                return season_code, anime_name.replace(season_chinese, '').strip()
        return None, anime_name

    def format_video_path(self, anime_name: str, season_num: Optional[str], episode_num: str) -> Path:
        """
        根據動畫名稱、季號與集數建立對應的下載路徑與檔案名稱
        """
        episode_str = f"{int(episode_num):02d}" if episode_num.isdigit() else episode_num
        if season_num and episode_num.isdigit():
            download_folder = self.root_download_path / anime_name / f"Season {int(season_num)}"
            video_path = download_folder / f"{anime_name} - S{season_num}E{episode_str}.mp4"
        elif season_num:
            download_folder = self.root_download_path / anime_name / f"Season {int(season_num)}"
            video_path = download_folder / f"{anime_name} - S{season_num} - {episode_str}.mp4"
        elif episode_num.isdigit():
            download_folder = self.root_download_path / anime_name / "Season 1"
            video_path = download_folder / f"{anime_name} - {episode_str}.mp4"
        else:
            download_folder = self.root_download_path / anime_name
            video_path = download_folder / f"{anime_name} - {episode_str}.mp4"
        download_folder.mkdir(parents=True, exist_ok=True)
        return video_path

    @staticmethod
    def check_file_exists(video_path: Path) -> None:
        """若檔案已存在則拋出例外"""
        if video_path.exists():
            raise FileExistsError(f"檔案已存在於 {video_path}")

    @staticmethod
    def validate_download(video_path: Path) -> None:
        """驗證下載檔案大小是否大於 1MB，否則刪除並拋出例外"""
        if video_path.stat().st_size < 1024 * 1024:
            video_path.unlink()
            raise Exception(f"{video_path.name} 檔案大小過小")

    def get_season_episodes(self, url: str) -> List[str]:
        """
        取得某季節頁面的所有集數連結
        """
        episodes: List[str] = []
        current_url: Optional[str] = url
        while current_url:
            response = self.session.post(current_url)
            soup = BeautifulSoup(response.text, 'lxml')
            for h2 in soup.find_all('h2', class_="entry-title"):
                a_tag = h2.find("a", attrs={"rel": "bookmark"})
                if a_tag:
                    episodes.append(a_tag.get('href'))
            nav_prev = soup.find('div', class_='nav-previous')
            current_url = nav_prev.find('a').get('href') if (nav_prev and nav_prev.find('a')) else None
        return episodes

    def create_download_directory(self) -> None:
        """建立下載根目錄"""
        self.root_download_path.mkdir(parents=True, exist_ok=True)

    def read_urls_from_file(self, file_path: str) -> List[str]:
        """從檔案中讀取所有連結（以 http 開頭）"""
        path = Path(file_path)
        if path.exists():
            with path.open("r", encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip().startswith("http")]
        return []

    @staticmethod
    def get_user_input_urls() -> List[str]:
        """從使用者輸入中獲取連結（以逗號分隔）"""
        user_input = input("? 請輸入 Anime1 URL（多個連結以逗號分隔）：")
        return [url.strip() for url in user_input.split(',') if url.strip()]

    def classify_urls(self, anime_urls: List[str]) -> List[str]:
        """依照連結格式展開季節頁面為單集連結"""
        url_list: List[str] = []
        for anime_url in anime_urls:
            anime_url = anime_url.strip()
            decoded_url = urllib.parse.unquote(anime_url)
            if re.search(r"anime1\.me/category/.*", anime_url, re.I):
                season_episodes = self.get_season_episodes(anime_url)
                url_list.extend(season_episodes)
                if season_episodes:
                    self.styled_print(f"已加入 {len(season_episodes)} 部影片：", color=Fore.CYAN, style=Style.BRIGHT, suffix=f"{decoded_url}")
                else:
                    self.styled_print("找不到任何影片：", color=Fore.RED, style=Style.BRIGHT, suffix=f"{anime_url}")
            elif re.search(r"anime1\.me/\d+", anime_url, re.I):
                url_list.append(anime_url)
                self.styled_print(f"已加入單集影片：", color=Fore.CYAN, style=Style.BRIGHT, suffix=f"{decoded_url}")
            else:
                self.styled_print(f"無法支援的連結：", color=Fore.RED, style=Style.BRIGHT, suffix=f"{anime_url}")
        return url_list

    def process_video_name(self, video_name: str) -> Path:
        """
        整合影片名稱處理流程：清理、解析、檢查檔案是否存在，最後返回下載路徑
        """
        clean_name = self.sanitize_video_name(video_name)
        anime_name, episode_num = self.parse_video_name(clean_name)
        season_num, anime_name = self.get_and_replace_season_number(anime_name)
        video_path = self.format_video_path(anime_name, season_num, episode_num)
        self.check_file_exists(video_path)
        return video_path

    def download_episode(self, url: str) -> None:
        """
        下載單一動畫集數：
          1. 取得影片資料與連結
          2. 根據檔案副檔名決定下載方式
          3. 下載完成後驗證檔案
        """
        response = self.session.post(url)
        soup = BeautifulSoup(response.text, 'lxml')
        video_tag = soup.find('video', class_='video-js')
        if not video_tag:
            raise Exception("找不到影片標籤")
        data_apireq = video_tag.get('data-apireq')
        if not data_apireq:
            raise Exception("找不到 data-apireq 資料")

        payload = f'd={data_apireq}'
        api_response = self.session.post('https://v.anime1.me/api', data=payload)
        api_data = api_response.json()
        video_src = api_data.get('s', [{}])[0].get('src')
        if not video_src:
            raise Exception("無法取得影片連結")
        video_url = f'https:{video_src}'

        # 解析 cookie 資訊
        set_cookie = api_response.headers.get('set-cookie', '')
        cookie_e = re.search(r"e=(.*?);", set_cookie, re.I)
        cookie_p = re.search(r"p=(.*?);", set_cookie, re.I)
        cookie_h = re.search(r"HttpOnly, h=(.*?);", set_cookie, re.I)
        if not (cookie_e and cookie_p and cookie_h):
            raise Exception("無法解析 cookie")
        cookies = f"e={cookie_e.group(1)};p={cookie_p.group(1)};h={cookie_h.group(1)};"
        download_headers = {
            "Accept": "*/*",
            "Accept-Encoding": 'identity;q=1, *;q=0',
            "Accept-Language": 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            "Cookie": cookies,
            "DNT": '1',
            "User-Agent": self.HEADERS.get('user-agent', '')
        }

        title_tag = soup.find('h2', class_="entry-title")
        if not title_tag:
            raise Exception("找不到影片標題")
        title = title_tag.text.strip()
        video_path = self.process_video_name(title)

        # 根據檔案副檔名選擇下載方式
        video_suffix = video_url.split('.')[-1].lower()
        if video_suffix == 'mp4':
            self.download_mp4(video_url, video_path, download_headers)
        elif video_suffix == 'm3u8':
            self.download_ts(video_url, video_path, download_headers)
        else:
            raise ValueError(f"影片連結的副檔名不支援：{video_suffix}")

        self.validate_download(video_path)
        self.styled_print(f"成功下載：", color=Fore.GREEN, style=Style.BRIGHT, suffix=f"{video_path.name}")

    def download_mp4(self, download_url: str, video_path: Path, headers: dict) -> None:
        """
        下載 mp4 影片並利用 Rich Progress 顯示進度，
        以檔案總位元組數作為進度任務總量。
        使用以 . 開頭的暫存檔，下載前若存在則清除。
        """
        response = self.session.get(download_url, headers=headers, stream=True)
        if response.status_code != 200:
            raise ConnectionError(f"下載失敗，狀態碼：{response.status_code}")

        total_bytes = int(response.headers.get('Content-Length', 0))
        if total_bytes == 0:
            raise ValueError("Content-Length 為零，無法下載檔案。")

        # 使用以 . 開頭的暫存檔
        temp_video_path = video_path.with_name("." + video_path.name)
        if temp_video_path.exists():
            temp_video_path.unlink()

        chunk_size = 10240  # 10KB
        task_id = self.progress.add_task(f"{video_path.name}", total=total_bytes)
        with temp_video_path.open('wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    self.progress.update(task_id, advance=len(chunk))
        temp_video_path.rename(video_path)
        self.progress.remove_task(task_id)

    def download_ts(self, download_url: str, video_path: Path, headers: dict) -> None:
        """
        下載 m3u8 格式影片：解析 ts 檔案連結，並行下載後利用 ffmpeg 轉檔為 mp4。
        使用 Rich Progress 以檔案數量作為進度單位。
        """
        def download_single_ts(ts_url: str, ts_path: Path) -> None:
            ts_response = self.session.get(ts_url, headers=headers)
            ts_path.write_bytes(ts_response.content)

        ts_links: Dict[str, str] = self.parse_m3u8_from_url(download_url, headers)
        ts_folder = Path(".ts")
        ts_folder.mkdir(exist_ok=True)
        m3u8_playlist_path = ts_folder / "playlist.m3u8"
        with m3u8_playlist_path.open('w', encoding='utf-8') as f:
            for ts_name in ts_links.keys():
                ts_path_str = (ts_folder / ts_name).resolve().as_posix()
                f.write(f"file '{ts_path_str}'\n")

        total_ts = len(ts_links)
        task_id = self.progress.add_task(f"{video_path.name} (TS)", total=total_ts)
        tasks: List[concurrent.futures.Future] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            for ts_name, ts_url in ts_links.items():
                ts_path = ts_folder / ts_name
                tasks.append(pool.submit(download_single_ts, ts_url, ts_path))
            # 逐一檢查任務完成情況並更新進度
            while tasks:
                for t in tasks.copy():
                    if t.done():
                        tasks.remove(t)
                        self.progress.update(task_id, advance=1)

        tmp_video_path = self.convert_ts_to_mp4(ts_folder, m3u8_playlist_path)
        tmp_video_path.rename(video_path)
        shutil.rmtree(ts_folder)
        self.progress.remove_task(task_id)

    @staticmethod
    def convert_ts_to_mp4(ts_folder: Path, m3u8_path: Path) -> Path:
        """
        使用 ffmpeg 轉換 ts 檔案為 mp4
        使用以 . 開頭的暫存檔，下載前若存在則清除。
        """
        ts_folder = ts_folder.resolve()
        tmp_video_path = ts_folder / ".temp.mp4"
        if tmp_video_path.exists():
            tmp_video_path.unlink()
        command = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', m3u8_path.as_posix(),
            '-c', 'copy',
            tmp_video_path.as_posix()
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            return tmp_video_path
        except subprocess.CalledProcessError as e:
            AnimeDownloader.styled_print(f"轉換 ts 檔案失敗，錯誤：{e}", color=Fore.RED, style=Style.BRIGHT)
            raise Exception(f"轉換 ts 檔案失敗，錯誤：{e}")

    def parse_m3u8_from_url(self, url: str, headers: dict) -> Dict[str, str]:
        """
        嘗試不同畫質後解析 m3u8 內容，返回 ts 檔案連結字典
        """
        for quality in ["1080p", "720p"]:
            new_url = url.replace("playlist", quality)
            response = self.session.get(new_url, headers=headers)
            if response.status_code == 200:
                m3u8_obj = m3u8.loads(response.text)
                ts_files = [segment['uri'] for segment in m3u8_obj.data.get('segments', [])]
                return {ts_file: new_url.replace("playlist.m3u8", ts_file) for ts_file in ts_files}
        raise ConnectionError(f"下載失敗，狀態碼：{response.status_code}")

    def run(self) -> None:
        """
        主執行流程：
          1. 讀取連結
          2. 分類並展開連結（若為季節頁面）
          3. 初始化 Rich Progress 物件顯示下載進度
          4. 單執行緒或多執行緒下載
        """
        anime_urls = self.read_urls_from_file(self.urls_path)
        if not anime_urls:
            anime_urls = self.get_user_input_urls()
        else:
            self.styled_print(f"從檔案讀取：{self.urls_path}，共有 {len(anime_urls)} 個連結",
                              color=Fore.WHITE, style=Style.BRIGHT)
        # 刪除重複連結
        anime_urls = list(set(anime_urls))
        url_list = self.classify_urls(anime_urls)
        self.styled_print(f"共有 {len(url_list)} 個連結需要下載", color=Fore.CYAN, style=Style.BRIGHT)
        start_time = time.time()

        self.progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            transient=True,
        )

        if self.use_multithreading:
            with self.progress:
                def download_wrapper(url: str) -> None:
                    try:
                        self.download_episode(url)
                    except Exception as e:
                        self.failed_list.append(f"{url}: {e}")
                        self.styled_print("失敗：", color=Fore.RED, style=Style.BRIGHT,
                                          suffix=f"{url}: {e}")
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    executor.map(download_wrapper, url_list)
        else:
            with self.progress:
                for url in url_list:
                    try:
                        self.download_episode(url)
                    except Exception as e:
                        self.failed_list.append(f"{url}: {e}")
                        self.styled_print("失敗：", color=Fore.RED, style=Style.BRIGHT,
                                          suffix=str(e))

        if self.failed_list:
            self.styled_print(f"\n{'='*10}下載失敗清單{'='*10}", color=Fore.RED, style=Style.BRIGHT)
            print("\n".join(self.failed_list))
        else:
            self.styled_print("所有影片下載完成！", color=Fore.GREEN, style=Style.BRIGHT)

        total_time = (time.time() - start_time)
        m, s = divmod(total_time, 60)
        success_count = len(url_list) - len(self.failed_list)
        self.styled_print("共耗時：", color=Fore.CYAN, style=Style.BRIGHT,
                          suffix=f"{int(m)} 分 {int(s)} 秒（成功：{success_count}，失敗：{len(self.failed_list)}）")


if __name__ == '__main__':
    try:
        downloader = AnimeDownloader()
        downloader.run()
    except Exception as err:
        AnimeDownloader.styled_print("程式發生錯誤：", color=Fore.RED, style=Style.BRIGHT, suffix=str(err))
        sys.exit(1)
