import math
import numpy as np
import pandas as pd
from scipy.stats import norm

FLOOR_HEIGHT = 4.0

ITEMS = {
 "00":("Sofa + Coffee Table",0.6,3000,True),
 "01":("TV + TV Cabinet",1.0,2000,True),
 "02":("Floor-standing Air Conditioner",0.3,3200,True),
 "03":("Wall-mounted Air Conditioner",2.0,1800,False),
 "04":("Refrigerator",0.3,1500,True),
 "05":("Gas Stove",0.1,600,False),
 "06":("Microwave Oven",0.2,300,True),
 "07":("Kitchen Cabinet",0.8,800,False),
 "08":("Dining Table + Chairs",0.6,1000,True),
 "09":("Electric Water Heater",0.5,600,False),
 "10":("Washing Machine",0.5,700,True),
 "11":("Bed + Bedside Table",0.4,1000,True),
 "12":("Wardrobe",1.0,1200,False),
 "13":("Desk + Chair",0.7,350,True),
 "14":("Bookshelf / Bookcase",1.0,260,False),
 "15":("Storage Rack / Shelf",1.0,140,False),
}

def infer_floors(row):
    for f in ["floors","floor","Floor","层数"]:
        if f in row.index and pd.notna(row[f]):
            try: return max(1, int(round(float(row[f]))))
            except: pass
    # V19-compatible fallback for classified buildings: infer from Height.
    if "Height" in row.index:
        try: return max(1, min(3, int(round(float(row["Height"])/3.2))))
        except: pass
    return 1

def infer_area(row):
    for f in ["buildingAr","Shape_Area","area"]:
        if f in row.index and pd.notna(row[f]):
            try:
                v=float(row[f])
                if v>1: return v
            except: pass
    try:
        # Geometry is expected in projected CRS in the source layer.
        return max(float(row.geometry.area), 30.0)
    except:
        return 80.0

def damage_probability(depth, floors):
    if depth <= 0: return 0.0
    depth=max(float(depth),0.001)
    if floors==1: mu,sigma=0.225056615795655,0.293792349
    elif floors==2: mu,sigma=1.175546133,0.281157956404193
    else: mu,sigma=1.409692588,0.222518876
    return round(float(np.clip(norm.cdf((math.log(depth)-mu)/sigma)*100,0,100)),2)

def floor_depths(H):
    H=max(float(H),0)
    return {"1F":min(H,4.0),
            "2F":max(0,min(H-4.0,4.0)),
            "3F":max(0,min(H-8.0,4.0))}

def inventory(area, floors):
    # V19/V18 report rule: B = clamp(round(S*N*0.018), 2, 8).
    bedrooms=max(2,min(8,int(round(area*floors*0.018))))
    inv={"1F":{},"2F":{},"3F":{}}
    # Common living/kitchen inventory.
    common={"00":1,"01":1,"02":1,"03":1,"04":1,"05":1,"06":1,
            "07":1,"08":1,"09":1,"10":1,"15":2}
    inv["1F"].update(common)

    # Bedroom assets are assigned to upper occupied floors when possible.
    bed_floor="1F" if floors==1 else ("2F" if floors==2 else "3F")
    inv[bed_floor]["11"]=bedrooms
    inv[bed_floor]["12"]=bedrooms
    inv[bed_floor]["13"]=max(1, bedrooms//2)
    inv[bed_floor]["14"]=max(1, bedrooms//2)
    return {k:v for k,v in inv.items() if int(k[0])<=min(floors,3)}

def household_loss(inv, H, warning=False):
    work={f:d.copy() for f,d in inv.items()}
    if warning and work:
        max_floor=next((f for f in ["3F","2F","1F"] if f in work and work[f]),"1F")
        for floor in list(work):
            if floor==max_floor: continue
            for code in list(work[floor]):
                if ITEMS[code][3] and work[floor][code]>0:
                    work[max_floor][code]=work[max_floor].get(code,0)+work[floor][code]
                    work[floor][code]=0
    fd=floor_depths(H)
    loss=0.0
    damaged=0
    total=0
    total_value=0.0
    for floor,things in work.items():
        d=fd.get(floor,0)
        for code,qty in things.items():
            if qty<=0: continue
            _,threshold,price,_=ITEMS[code]
            total += qty
            total_value += qty*price
            if d>0 and d>=threshold:
                damaged += qty
                loss += qty*price
    return loss, damaged, total, total_value

def depth_category(h):
    if h<0.5:return 0
    if h<1.0:return 1
    if h<2.0:return 2
    return 3

def duration_category(hours):
    if hours<12:return 0
    if hours<24:return 1
    if hours<72:return 2
    return 3

def injury_probability(depth, duration_h, damage_pct, floors,
                       vulnerable_ratio=0.0, warning_time=0.0):
    # Preserve project dry-building rule.
    if depth <= 0.01:
        return 0.0
    X1=depth_category(depth)
    X2=duration_category(duration_h)
    X3=0.5*X1
    X4=float(damage_pct)/100.0
    X5=float(floors)
    X6=float(np.clip(vulnerable_ratio,0,1))
    if warning_time<=0:X7=0
    elif warning_time<0.5:X7=1
    elif warning_time<2:X7=2
    else:X7=3
    Z=-1.2+0.8*X1+0.5*X2+0.6*X3+1.5*X4-0.3*X5+1.0*X6-0.4*X7
    P=1/(1+math.exp(-Z))
    return round(float(np.clip(P,0,1)*100),1)

def assess_buildings(buildings, current_depth, cumulative_depth, duration_h):
    out=buildings.copy()
    out["cur_depth"]=np.asarray(current_depth,float)
    out["cum_depth"]=np.asarray(cumulative_depth,float)
    damages=[]; losses=[]; risks=[]; warn_losses=[]; reductions=[]; red_pct=[]
    floors_list=[]; areas=[]
    for (_,row), cur, H in zip(out.iterrows(),out["cur_depth"],out["cum_depth"]):
        floors=infer_floors(row); area=infer_area(row)
        damage=damage_probability(H,floors)
        inv=inventory(area,floors)
        loss,_,_,_=household_loss(inv,H,False)
        wl,_,_,_=household_loss(inv,H,True)
        reduction=max(0.0,loss-wl)
        # Use household fields when available; otherwise no vulnerable-person uplift.
        pop=0; vulnerable=0
        for f in ["pop","population","人数"]:
            if f in row.index:
                try: pop=float(row[f])
                except: pass
        for f in ["elderly","children","老年人","儿童"]:
            if f in row.index:
                try: vulnerable += float(row[f])
                except: pass
        vr=vulnerable/pop if pop>0 else 0.0
        risk=injury_probability(H,duration_h,damage,floors,vr,0.0)
        floors_list.append(floors); areas.append(area); damages.append(damage)
        losses.append(loss); warn_losses.append(wl); reductions.append(reduction)
        red_pct.append(100*reduction/loss if loss>0 else 0)
        risks.append(risk)
    out["floors_calc"]=floors_list
    out["area_calc"]=areas
    out["damage_pct"]=damages
    out["loss_total"]=losses
    out["warn_loss"]=warn_losses
    out["loss_red"]=reductions
    out["red_pct"]=red_pct
    out["inj_prob"]=risks
    return out
