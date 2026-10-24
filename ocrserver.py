import bottle
import ocr
import io
import yaml
from PIL import Image
from manga_ocr_cpt_lib import MangaOcrCPT
app = bottle.Bottle()
mocr = MangaOcrCPT("flex", local_files_only=True)
@app.route("/ocr", method="POST")
def ocr_route():
    image = io.BytesIO(bottle.request.body.read())
    if not image:
        return {"error": "No image uploaded"}
    
    result = mocr(Image.open(image))
    return {"result": result}

if __name__ == "__main__":
    bottle.run(app, host="0.0.0.0", port=8379)
