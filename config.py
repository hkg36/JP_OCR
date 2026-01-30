import yaml
with open("conf.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)
gcloud_api_key = config["key"]["gcloud"]
huggingface_token = config["key"]["hf_token"]