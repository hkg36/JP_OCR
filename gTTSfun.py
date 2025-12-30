import httpx
from io import BytesIO
import re

proxy = "http://127.0.0.1:10808"
session = None
def japanese_tts(text: str) -> BytesIO:
    """
    将日语文本转换为语音，返回 BytesIO 对象。
    
    :param text: 要转换的日语文本
    :return: BytesIO 对象，包含 MP3 音频数据
    """
    text = re.sub(r'[\s\u3000]+', '', text)
    text = text.strip()
    # Google TTS API 端点和参数
    url = "https://translate.google.com/translate_tts"
    params_base = {
        "ie": "UTF-8",
        "client": "tw-ob",
        "tl": "ja",
        "ttsspeed": "1"
    }
    global session,proxy
    if session is None:
        session = httpx.Client(timeout=10.0,http2=True,proxy=proxy)
    params = params_base.copy()
    params["q"] = text
    response = session.get(url, params=params)
    response.raise_for_status()
    output=BytesIO(response.content)
    return output

if __name__ == "__main__":
    import pygame
    # 示例使用
    audio = japanese_tts("こんにちは、世界。今日はいい天気です。")
    # with open("output.mp3", "wb") as f:
    #     f.write(audio.getvalue())
    pygame.mixer.init()
    pygame.mixer.music.load(audio)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)