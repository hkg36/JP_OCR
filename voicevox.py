import httpx
import subprocess
import time
import sys
import os
from io import BytesIO
from pathlib import Path
from loguru import logger

import signal

# 配置
#VOICEVOX_EXE = r"C:\path\to\your\voicevox_engine\voicevox_engine.exe"  # 改成实际路径
#VOICEVOX_ARGS = ["--use_gpu"]  # 根据需要加参数，如 --port 50022 如果想改端口
ENGINE_URL = "http://localhost:50021"  # 如果改了端口这里也要同步改
CHECK_TIMEOUT = 5  # 秒

voicevox_proc: subprocess.Popen | None = None
requests=httpx.Client(timeout=CHECK_TIMEOUT)
def is_voicevox_running() -> bool:
    """检测 VOICEVOX 是否已经在运行"""
    try:
        # 最轻量的检查：/version 端点
        response = requests.get(f"{ENGINE_URL}/version")
        if response.status_code == 200:
            logger.info(f"VOICEVOX 已运行 (版本: {response.text.strip()})")
            return True
    except (httpx.RequestError, httpx.TimeoutException):
        pass  # 连接失败/超时 → 认为没运行

    return False

def start_voicevox_if_needed(VOICEVOX_EXE: str, VOICEVOX_ARGS: list[str]):
    global voicevox_proc

    if is_voicevox_running():
        logger.info("VOICEVOX 已存在，直接复用（不启动新进程）")
        voicevox_proc = None  # 表示我们没启动新进程，关闭时不杀
        return

    logger.info("VOICEVOX 未运行，正在启动...")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    voicevox_proc = subprocess.Popen(
        [VOICEVOX_EXE] + VOICEVOX_ARGS,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,  # 静默启动，或改成 PIPE 看日志
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=Path(VOICEVOX_EXE).parent,
    )
    return voicevox_proc

def stop_voicevox():
    global voicevox_proc
    if voicevox_proc is None or voicevox_proc.poll() is not None:
        return

    logger.info("关闭 VOICEVOX...")
    try:
        voicevox_proc.send_signal(signal.CTRL_BREAK_EVENT)
        voicevox_proc.wait(timeout=6)
        logger.info("VOICEVOX 已正常退出")
    except subprocess.TimeoutExpired:
        logger.warning("超时 → 强制杀死进程树")
        try:
            os.system(f'taskkill /PID {voicevox_proc.pid} /T /F')
        except Exception as e:
            voicevox_proc.kill()
    except Exception as e:
        logger.error(f"关闭异常: {e}")
        voicevox_proc.kill()

    voicevox_proc = None

def japanese_tts(
    text: str = "こんにちは、これはテスト文です。",
    speaker: int = 8,
    speed_scale: float = 1.0,
    output_sampling_rate: int = 24000,
) -> BytesIO:
    # 第一步：生成 audio_query（包含 sampling_rate）
    query_url = f"{ENGINE_URL}/audio_query"
    query_params = {"text": text, "speaker": speaker}
    response = requests.post(query_url, params=query_params)
    if response.status_code != 200:
        logger.error("Audio query 失败:", response.text)
        return None

    query = response.json()
    # 固定语速，避免引擎侧预设导致播放听感异常偏快。
    query["speedScale"] = float(speed_scale)
    # 显式提升输出采样率，降低播放器按 48k 假设解码时产生“倍速感”的风险。
    query["outputSamplingRate"] = int(output_sampling_rate)


    # 第二步：合成音频
    synth_url = f"{ENGINE_URL}/synthesis"
    synth_params = {"speaker": speaker}
    audio_response = requests.post(synth_url, params=synth_params, json=query)
    if audio_response.status_code != 200:
        logger.error("Synthesis 失败:", audio_response.text)
        return None

    audio_bytes = audio_response.content

    # 按 WAV 文件流加载，避免把带文件头的数据当成原始 PCM 缓冲区解释
    audio_stream = BytesIO(audio_bytes)
    audio_stream.seek(0)
    return audio_stream
# 主程序示例
if __name__ == "__main__":
    try:
        start_voicevox_if_needed("D:\\apps\\voicevox_engine-windows-cpu\\run.exe", [])

        time.sleep(3)  # 模拟
        running=is_voicevox_running()
        logger.info(f"主程序检测 VOICEVOX 运行状态: {running}")
        if running:
            audio_stream = japanese_tts("今話しているのはこの俺お前の主ユウヤだ",68,speed_scale=0.9)
            if audio_stream is not None:
                logger.info(f"生成的音频流大小: {audio_stream.getbuffer().nbytes} 字节")
                import pygame
                pygame.mixer.init()
                pygame.mixer.music.load(audio_stream)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)

    except Exception as e:
        logger.error(f"主程序异常: {e}")
    finally:
        stop_voicevox()