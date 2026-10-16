#!/usr/bin/env python3
"""Offline IEC drawing kit: uniform mm geometry and explicit geometric docking points."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44, Vec3

GRID = 2.5
SCALE = 25 / 13.2291666667
LAYERS = {'UGO_GEOMETRY': (30, 1), 'UGO_LABELS': (20, 1),
          'CONNECTION_POINTS': (13, 0), 'WIRES': (30, 1),
          'FRAME': (50, 1), 'FRAME_TEXT': (20, 1), 'GUIDES': (13, 0)}

def setup():
    d = ezdxf.new('R2013')
    for key,value in {'$INSUNITS': 4, '$MEASUREMENT': 1, '$LUNITS': 2,
                     '$LUPREC': 2, '$LWDISPLAY': True, '$PDMODE': 35, '$PDSIZE': .6,
                     '$OSMODE': 1 + 8 + 64, '$INSBASE': (0,0,0)}.items():d.header[key]=value
    d.styles.new('SCHEME', dxfattribs={'font': 'Arial.ttf'})
    for name,(weight,plot) in LAYERS.items():
        d.layers.new(name, dxfattribs={'color':7,'lineweight':weight,'plot':plot})
    v=d.viewports.get_config('*Active')[0]
    v.dxf.snap_spacing=(GRID,GRID);v.dxf.grid_spacing=(GRID,GRID)
    v.dxf.snap_base=(0,0);v.dxf.flags=3
    d.appids.new('IEC_DOCKING')
    return d

def endpoints(e):
    if e.dxftype()=='LINE': return [e.dxf.start,e.dxf.end]
    if e.dxftype()=='ARC': return [e.start_point,e.end_point]
    return []

def docking(entities):
    """Free axial outer lead ends; geometric candidates, never inferred pin functions."""
    ext=bbox.extents(entities);pts=[p for e in entities for p in endpoints(e)]
    result=[]
    for e in entities:
        if e.dxftype()!='LINE':continue
        a,b=e.dxf.start,e.dxf.end;delta=b-a
        if delta.magnitude<.7:continue
        vertical=abs(delta.x)<.001;horizontal=abs(delta.y)<.001
        if not(vertical or horizontal):continue
        for field,p,q in [('start',a,b),('end',b,a)]:
            if sum((p-v).magnitude<.001 for v in pts)!=1:continue
            side=None
            if vertical and p.y>q.y and p.y>=ext.extmax.y-ext.size.y*.2:side='TOP'
            elif vertical and p.y<q.y and p.y<=ext.extmin.y+ext.size.y*.2:side='BOTTOM'
            elif horizontal and p.x<q.x and p.x<=ext.extmin.x+ext.size.x*.2:side='LEFT'
            elif horizontal and p.x>q.x and p.x>=ext.extmax.x-ext.size.x*.2:side='RIGHT'
            if side:result.append({'entity':e,'field':field,'point':p,'side':side})
    return sorted(result,key=lambda r:({'TOP':0,'LEFT':1,'RIGHT':2,'BOTTOM':3}[r['side']],r['point'].x,-r['point'].y))

def normalized_block(doc, source, row):
    old=source.blocks['IEC_'+row['id']]
    entities=[e.copy() for e in old if e.dxftype()!='ATTDEF']
    ports=docking(entities)
    if row['code'] in {'QF','KM','KK','K','KT','KV','M','T','TA','HL'}:
        ports=[p for p in ports if p['side'] in ('TOP','BOTTOM')]
    ext0=bbox.extents(entities)
    anchor=ports[0]['point'] if ports else (ext0.extmin+ext0.extmax)*.5
    transform=Matrix44.translate(-anchor.x,-anchor.y,0) @ Matrix44.scale(SCALE)
    raw=[(p,transform.transform(p['point'])) for p in ports]
    for e in entities:
        e.transform(transform);e.dxf.layer='UGO_GEOMETRY'
        e.dxf.color=256;e.dxf.lineweight=-1;e.dxf.linetype='BYLAYER'
    b=doc.blocks.new('IEC_'+row['id'])
    for e in entities:b.add_entity(e)
    port_data=[]
    for i,(p,v) in enumerate(raw):
        snapped=Vec3(round(v.x/GRID)*GRID,round(v.y/GRID)*GRID,0)
        setattr(p['entity'].dxf,p['field'],snapped)
        other='end' if p['field']=='start' else 'start'
        inner=getattr(p['entity'].dxf,other)
        if p['side'] in ('TOP','BOTTOM'):inner=Vec3(snapped.x,inner.y,0)
        else:inner=Vec3(inner.x,snapped.y,0)
        setattr(p['entity'].dxf,other,inner)
        marker=b.add_point(snapped,dxfattribs={'layer':'CONNECTION_POINTS'})
        marker.set_xdata('IEC_DOCKING',[(1000,f'P{i+1}'),(1000,p['side'])])
        port_data.append({'id':f'P{i+1}','side':p['side'],'position':list(snapped),
                          'adjustmentMm':(snapped-v).magnitude,'role':'geometric_candidate'})
    if not port_data:
        marker=b.add_point((0,0),dxfattribs={'layer':'CONNECTION_POINTS'})
        marker.set_xdata('IEC_DOCKING',[(1000,'BASE'),(1000,'CENTER_ONLY')])
    ext=bbox.extents(entities)
    # No visible description beneath a wire: retain it as an editable hidden attribute.
    if row['tag']:
        b.add_attdef('TAG',(ext.extmin.x-2.5,(ext.extmin.y+ext.extmax.y)/2),
            text=row['tag'],height=3.5,dxfattribs={'layer':'UGO_LABELS','style':'SCHEME',
            'halign':2,'align_point':(ext.extmin.x-2.5,(ext.extmin.y+ext.extmax.y)/2,0)})
    b.add_attdef('NAME_RU',(0,0),text=row['ru'],height=2.5,
        dxfattribs={'layer':'UGO_LABELS','style':'SCHEME','flags':1})
    reviewed=row['id'] in {'083','099','100','111','301','302','303','304','305','306','307','308','311','351','352','353'}
    return {'id':row['id'],'name':row['ru'],'block':b.name,'code':row['code'],
            'defaultTag':row['tag'],'ports':port_data,'sourceScale':SCALE,
            'bbox':[list(ext.extmin),list(ext.extmax)],
            'status':'reviewed_geometry' if reviewed else 'candidate_points_need_review'}

def insert(doc,item,pos,tag=None):
    ins=doc.modelspace().add_blockref(item['block'],pos,dxfattribs={'layer':'UGO_GEOMETRY'})
    ins.add_auto_attribs({'TAG':tag or item['defaultTag'],'NAME_RU':item['name']})
    return ins

def frame(doc):
    ms=doc.modelspace()
    ms.add_lwpolyline([(20,5),(415,5),(415,292),(20,292)],close=True,dxfattribs={'layer':'FRAME'})
    def line(a,b):ms.add_line(a,b,dxfattribs={'layer':'FRAME'})
    def text(s,x,y,h=2.5):ms.add_text(s,dxfattribs={'insert':(x,y),'height':h,'style':'SCHEME','layer':'FRAME_TEXT'})
    # A3 drafting title block; editable project fields, no imported project values.
    x=230;y=5
    for yy in [5,15,25,40,60]:line((x,yy),(415,yy))
    line((x,y),(x,60));line((295,y),(295,60))
    for xx in [245,265,280]:line((xx,5),(xx,40))
    line((375,5),(375,25));line((395,5),(395,25))
    for s,xx,yy in [('Разраб.',231,32),('Проверил',231,22),('Н.контр.',231,12),('Утв.',231,7),
                    ('Лист',376,19),('Листов',396,19),('Подпись',266,42),('Дата',281,42)]:text(s,xx,yy)
    b=doc.blocks.new('PROJECT_FIELDS')
    fields=[('DOC','Обозначение документа',(298,47),4),('TITLE','Схема электрическая принципиальная',(298,31),3),
            ('PROJECT','Наименование проекта',(298,10),3),('SHEET','1',(383,8),3.5),('SHEETS','1',(402,8),3.5)]
    for tag,value,p,h in fields:b.add_attdef(tag,p,text=value,height=h,dxfattribs={'layer':'FRAME_TEXT','style':'SCHEME'})
    ms.add_blockref(b.name,(0,0),dxfattribs={'layer':'FRAME_TEXT'}).add_auto_attribs({t:v for t,v,p,h in fields})
    for xx in range(20,416,25):ms.add_point((xx,275),dxfattribs={'layer':'GUIDES'})
    doc.set_modelspace_vport(310,center=(217.5,148.5))
    layout=doc.layouts.get('Layout1')
    layout.page_setup(size=(420,297),margins=(0,0,0,0),units='mm',scale=(1,1))
    vp=layout.add_viewport(center=(210,148.5),size=(420,297),view_center_point=(210,148.5),view_height=297,
                        dxfattribs={'layer':'GUIDES','flags':16384})
    vp.dxf.snap_spacing=(GRID,GRID);vp.dxf.grid_spacing=(GRID,GRID)
    for v in layout.query('VIEWPORT'):
        if v.dxf.id==1:v.dxf.layer='0'

def demo(doc,items):
    byid={r['id']:r for r in items};ms=doc.modelspace();segments=[]
    placements=[('307',(70,255),'QF1'),('308',(70,205),'KM1'),('353',(70,155),'KK1'),('294',(70,105),'M1')]
    parts=[]
    for sid,pos,tag in placements:
        item=byid[sid];insert(doc,item,pos,tag)
        parts.append({side:sorted([(p['position'][0]+pos[0],p['position'][1]+pos[1]) for p in item['ports'] if p['side']==side]) for side in ['TOP','BOTTOM']})
    for upstream,downstream in zip(parts,parts[1:]):
        assert len(upstream['BOTTOM'])==len(downstream['TOP'])==3
        for a,b in zip(upstream['BOTTOM'],downstream['TOP']):
            points=[a,(a[0],b[1]+5),(b[0],b[1]+5),b]
            for p,q in zip(points,points[1:]):
                if p!=q:ms.add_line(p,q,dxfattribs={'layer':'WIRES'});segments.append([p,q])
    for sid,pos,tag in [('099',(250,235),'K1'),('111',(250,185),'KT1')]:insert(doc,byid[sid],pos,tag)
    a=(250,210);b=(250,185)
    ms.add_line(a,b,dxfattribs={'layer':'WIRES'});segments.append([a,b])
    ms.add_mtext('Пример геометрической стыковки\nОбозначения и цепи уточняются для проекта',dxfattribs={'insert':(170,275),'width':220,'char_height':3,'layer':'UGO_LABELS','style':'SCHEME'})
    return segments

def build(source_dir:Path,output:Path):
    output.mkdir(parents=True,exist_ok=True)
    rows=json.loads((source_dir/'Каталог.json').read_text())
    template=setup();atlas=setup();items=[]
    for row in rows:
        source=ezdxf.readfile(source_dir/row['file'])
        item=normalized_block(template,source,row)
        normalized_block(atlas,source,row)
        single=setup();normalized_block(single,source,row);insert(single,item,(0,0))
        p=output/'DXF'/f"{row['id']} — {row['ru'].replace('/',' ')}.dxf";p.parent.mkdir(exist_ok=True)
        single.saveas(p);assert not ezdxf.readfile(p).audit().has_errors
        item['file']=str(p.relative_to(output));item['sha256']=hashlib.sha256((source_dir/row['file']).read_bytes()).hexdigest();items.append(item)
    max_width=max(r['bbox'][1][0]-r['bbox'][0][0] for r in items)
    max_height=max(r['bbox'][1][1]-r['bbox'][0][1] for r in items)
    cell_width=max(90,max_width+45);cell_height=max(100,max_height+45)
    for i,item in enumerate(items):
        pos=((i%5)*cell_width+25-item['bbox'][0][0],-(i//5)*cell_height-10-item['bbox'][1][1])
        insert(atlas,item,pos)
        atlas.modelspace().add_mtext(item['id']+' — '+item['name'],dxfattribs={'insert':((i%5)*cell_width+5,-(i//5)*cell_height-max_height-20),
            'width':cell_width-10,'char_height':2.5,'style':'SCHEME','layer':'UGO_LABELS'})
    frame(template);template.saveas(output/'Шаблон схемы А3.dxf')
    atlas.saveas(output/'Библиотека IEC 323.dxf')
    wires=demo(template,items);template.saveas(output/'Пример стыковки А3.dxf')
    manifest={'gridMm':GRID,'units':'mm','tagHeightMm':3.5,'insertionScale':1,
              'symbols':items,'demoSegments':wires,'frame':'A3 drafting frame; title block requires project acceptance'}
    (output/'Контракт библиотеки.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'symbols':len(items),'geometricPoints':sum(len(x['ports']) for x in items),
          'noPoints':sum(not x['ports'] for x in items)},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.source,a.output)
