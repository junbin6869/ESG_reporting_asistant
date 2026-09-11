from pathlib import Path
from PIL import Image, ImageDraw

src = Path(r"C:\Users\junbi\Document\FYP\.review2\final_pages")
out = Path(r"C:\Users\junbi\Document\FYP\.review2\final_contacts")
out.mkdir(parents=True, exist_ok=True)
files = sorted(src.glob('page-*.png'), key=lambda p: int(p.stem.split('-')[-1]))
cols, rows = 4, 4
thumb_w, thumb_h, pad, label_h = 220, 285, 18, 22
for start in range(0, len(files), cols*rows):
    batch = files[start:start+cols*rows]
    canvas = Image.new('RGB', (cols*(thumb_w+pad)+pad, rows*(thumb_h+label_h+pad)+pad), 'white')
    draw = ImageDraw.Draw(canvas)
    for i, path in enumerate(batch):
        im = Image.open(path).convert('RGB')
        im.thumbnail((thumb_w, thumb_h))
        col, row = i % cols, i // cols
        x = pad + col*(thumb_w+pad) + (thumb_w-im.width)//2
        y = pad + row*(thumb_h+label_h+pad)
        canvas.paste(im, (x,y))
        draw.text((pad+col*(thumb_w+pad), y+thumb_h+2), f"PDF p. {start+i+1}", fill='black')
    canvas.save(out / f"contact-{start+1:03d}-{start+len(batch):03d}.png")
