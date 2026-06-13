import bottle
import ocr
import io
import yaml
from PIL import Image
with open("conf.yaml", "r", encoding="utf-8") as f:
    conf = yaml.safe_load(f)
app = bottle.Bottle()
mocr = ocr.MangaOcr(local_files_only=True,force_cpu=True,pretrained_model_name_or_path=conf["ocr"]["local_model"])
@app.route("/ocr", method="POST")
def ocr_route():
    image = io.BytesIO(bottle.request.body.read())
    if not image:
        return {"error": "No image uploaded"}
    
    result = mocr(Image.open(image))
    return {"result": result}

if __name__ == "__main__":
    bottle.run(app, host="0.0.0.0", port=8379)
