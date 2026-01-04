import httpx
from io import BytesIO
import re

proxy = "http://127.0.0.1:10808"
session = None
def get_session():
    global session
    if session is None:
        session = httpx.Client(timeout=10.0,http2=True,proxy=proxy)
    return session
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
    session = get_session()
    params = params_base.copy()
    params["q"] = text
    response = session.get(url, params=params)
    response.raise_for_status()
    output=BytesIO(response.content)
    return output
def translate_with_api_key(text="Hello, world!", target="zh-CN", api_key="YOUR_API_KEY_HERE"):
    url = "https://translation.googleapis.com/language/translate/v2"
    params = {
        "q": text,
        "target": target,
        "key": api_key
    }
    session = get_session()
    response = session.post(url, data=params)
    response.raise_for_status()
    
    result = response.json()
    translated_text = result["data"]["translations"][0]["translatedText"]
    return translated_text
if __name__ == "__main__":
    import pygame
    import config
    # 示例使用
    srctext="こんにちは、世界。今日はいい天気です。"
    translated_text = translate_with_api_key(text=srctext, target="zh-CN", api_key=config.gcloud_api_key)
    print(f"翻译结果: {translated_text}")
    audio = japanese_tts(srctext)
    # with open("output.mp3", "wb") as f:
    #     f.write(audio.getvalue())
    pygame.mixer.init()
    pygame.mixer.music.load(audio)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)