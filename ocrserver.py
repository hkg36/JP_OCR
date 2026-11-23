import bottle
import io
from PIL import Image
import os
os.environ["HF_HUB_OFFLINE"] = "1"
from manga_ocr_cpt import MangaOcrCPT
app = bottle.Bottle()
mocr = MangaOcrCPT("flex")
@app.route("/ocr", method="POST")
def ocr_route():
    image = io.BytesIO(bottle.request.body.read())
    if not image:
        return {"error": "No image uploaded"}
    
    result = mocr(Image.open(image))
    return {"result": result}

if __name__ == "__main__":
    bottle.run(app, host="0.0.0.0", port=8379)
