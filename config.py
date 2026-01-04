import configparser
config = configparser.ConfigParser()
config.read("conf.ini", encoding="utf-8")
gcloud_api_key = config.get("key", "gcloud")