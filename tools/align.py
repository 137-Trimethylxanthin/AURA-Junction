# Align the dataset PNG to the SVG frame from plan extents; also room 3.04 edge lengths. Usage: python -I align.py <dxf> <png> <outdir>
import sys, ezdxf, numpy as np
from PIL import Image
dxf, png, out = sys.argv[1:4]
m = ezdxf.readfile(dxf).modelspace()
pts = [p for e in m.query('LWPOLYLINE') for p in e.get_points('xy')]
minx=min(p[0] for p in pts); maxx=max(p[0] for p in pts); miny=min(p[1] for p in pts); maxy=max(p[1] for p in pts)
im = Image.open(png).convert('L'); a = np.array(im)
H0, W0 = a.shape
# drawing area: inside frame, above title block
sub = a[90:int(H0*0.80), 90:W0-90]
ys, xs = np.where(sub < 200)
px0, px1 = xs.min()+90, xs.max()+90; py0, py1 = ys.min()+90, ys.max()+90
sx = (px1-px0)/(maxx-minx); sy = (py1-py0)/(maxy-miny)
print('png bbox', px0, px1, py0, py1, 'scale x,y px/m', round(sx,3), round(sy,3))
s = (sx+sy)/2
# SVG frame = extents +-0.6 m
fx0 = px0 - 0.6*s; fy0 = py0 - 0.6*s; fw = (maxx-minx+1.2)*s; fh = (maxy-miny+1.2)*s
crop = Image.open(png).convert('RGB').crop((round(fx0), round(fy0), round(fx0+fw), round(fy0+fh))).resize((1221,1220))
crop.save(out+'/original-crop.png', optimize=True)
# room 3.04 edges
from shapely.geometry import Point, Polygon
for e in m.query('LWPOLYLINE[layer=="ROOM_OUTLINE"]'):
    poly = Polygon(list(e.get_points('xy')))
    if poly.contains(Point(-4.01, 3.62)):
        c = list(poly.exterior.coords)
        print('room 3.04 area', round(poly.area,3), 'edges', [round(((c[i+1][0]-c[i][0])**2+(c[i+1][1]-c[i][1])**2)**.5,3) for i in range(len(c)-1)])
