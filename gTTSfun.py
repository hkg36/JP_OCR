import httpx
from io import BytesIO
import re
from collections import OrderedDict
from openai import OpenAI

class RecentCache:
    def __init__(self, capacity: int = 3):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key: str):
        """获取值，如果存在则标记为最近使用"""
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)  # 移到末尾，表示最近使用
        return self.cache[key]

    def put(self, key: str, value):
        """插入或更新值"""
        if key in self.cache:
            self.cache.move_to_end(key)  # 已存在，更新为最近
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)  # 移除最旧的（最前面）

def get_proxy():
    return "http://127.0.0.1:10808"
session = None
keepbuffer=3
recent_buffer_tts = RecentCache(capacity=keepbuffer)
recent_buffer_translate = RecentCache(capacity=keepbuffer)
def lookup_translation_cache(text: str):
    """从翻译缓存中查找文本的翻译结果"""
    return recent_buffer_translate.get(text)
def get_session():
    global session
    if session is None:
        session = httpx.Client(timeout=10.0,http2=True,proxy=get_proxy())
    return session
def japanese_tts(text: str) -> BytesIO:
    """
    将日语文本转换为语音，返回 BytesIO 对象。
    
    :param text: 要转换的日语文本
    :return: BytesIO 对象，包含 MP3 音频数据
    """
    text = re.sub(r'[\s\u3000]+', '', text)
    text = text.strip()
    global recent_buffer_tts
    buffer_res= recent_buffer_tts.get(text)
    if buffer_res is not None:
        print("使用缓存的 TTS 结果")
        return BytesIO(buffer_res)
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
    recent_buffer_tts.put(text,response.content)
    output=BytesIO(response.content)
    return output
def translate_with_api_key(text="Hello, world!", target="zh-CN", api_key="YOUR_API_KEY_HERE"):
    global recent_buffer_translate
    buffer_res= recent_buffer_translate.get(text)
    if buffer_res is not None:
        print("使用缓存的翻译结果")
        return buffer_res
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
    recent_buffer_translate.put(text, translated_text)
    return translated_text

__ai_client = None
def set_ai_client(base_url=None):
    global __ai_client
    __ai_client = OpenAI(
        base_url=base_url,   # 注意是 /v1
        api_key=""              # llama-server 不需要真实 key，随便填
    )
    
def translate_with_local_model(text="Hello, world!"):
    client = __ai_client
    if client is None:
        raise ValueError("AI client not set. Please call set_ai_client() first.")

    response = client.chat.completions.create(
        model="qwen2.5-1.5b",                  # 模型名随便写，llama-server 基本忽略或用文件名
        messages=[
            {
                "role": "system",
                "content": "将日语翻译成中文，保留原文细节，不要意译。"
            },
            {
                "role": "user",
                "content": text
            }
        ],
        temperature=0.3,
        max_tokens=8192
    )

    translated_text = response.choices[0].message.content
    return translated_text

__ali_ai_client = None
def set_ali_ai_client(api_key=None):
    global __ali_ai_client
    __ali_ai_client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
def translate_with_ali(text="Hello, world!", source_lang="Japanese", target_lang="Chinese"):
    buffer_res= recent_buffer_translate.get(text)
    if buffer_res is not None:
        print("使用缓存的翻译结果")
        return buffer_res
    client = __ali_ai_client
    if client is None:
        raise ValueError("Ali AI client not set. Please call set_ali_ai_client() first.")

    messages = [
        {
            "role": "user",
            "content": text
        }
    ]
    translation_options = {
        "source_lang": source_lang,
        "target_lang": target_lang
    }
    response = client.chat.completions.create(
        model="qwen-mt-flash",
        messages=messages,
        extra_body={
            "translation_options": translation_options
        }
    )

    translated_text = response.choices[0].message.content
    recent_buffer_translate.put(text, translated_text)
    return translated_text

if __name__ == "__main__":
    import yaml
    with open("conf.yaml", "r", encoding="utf-8") as f:
         config = yaml.safe_load(f)
    gcloud_api_key = config["key"]["gcloud"]
    src="期待以上に資料を見つけられた"
    set_ali_ai_client(config["key"]["ali_key"])
    translated = translate_with_ali(src)
    print(f"阿里云百炼翻译结果: {translated}")
    exit(0)

    # 示例使用
    srctext="こんにちは、世界。今日はいい天気です。"
    translated_text = translate_with_api_key(text=srctext, target="zh-CN", api_key=gcloud_api_key)
    print(f"翻译结果: {translated_text}")
    audio = japanese_tts(srctext)
    with open("output.mp3", "wb") as f:
        f.write(audio.getvalue())
    print("语音已写入 output.mp3")