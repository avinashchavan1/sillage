"""Seed a demo database so you can explore the app before adding your own collection.

    python -m scripts.demo_data            # writes data/demo.db
    SILLAGE_DB=data/demo.db uvicorn app.main:app

(Your real collection is filled by lookups from the web; this is only sample data.)
"""

import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from app import db

A = lambda *pairs: [{"name": n, "strength": s} for n, s in pairs]  # noqa: E731

DEMO = [
    dict(name="Tobacco Vanille", brand="Tom Ford", year=2007, family="Amber Spicy", sentiment=2, status="owned",
         accords=A(("tobacco", 1), ("vanilla", .9), ("sweet", .8), ("warm spicy", .7), ("woody", .5)),
         top_notes=["Tobacco Leaf", "Spicy Notes"], middle_notes=["Vanilla", "Cacao", "Tonka Bean", "Tobacco Blossom"],
         base_notes=["Dried Fruits", "Woody Notes"], perfumers=["Olivier Gillotin"]),
    dict(name="Baccarat Rouge 540", brand="Maison Francis Kurkdjian", year=2015, family="Amber Floral", sentiment=1,
         status="decant", accords=A(("amber", 1), ("woody", .8), ("warm spicy", .6), ("sweet", .5)),
         top_notes=["Saffron", "Jasmine"], middle_notes=["Amberwood", "Ambergris"], base_notes=["Fir Resin", "Cedar"],
         perfumers=["Francis Kurkdjian"]),
    dict(name="Grand Soir", brand="Maison Francis Kurkdjian", year=2016, family="Amber", sentiment=2, status="owned",
         accords=A(("amber", 1), ("vanilla", .7), ("balsamic", .7), ("warm spicy", .5)),
         top_notes=["Spanish Labdanum"], middle_notes=["Benzoin", "Amber"], base_notes=["Tonka Bean", "Vanilla"],
         perfumers=["Francis Kurkdjian"]),
    dict(name="Acqua di Giò", brand="Giorgio Armani", year=1996, family="Aromatic Aquatic", sentiment=-1, status="sold",
         accords=A(("citrus", 1), ("aquatic", .9), ("marine", .7), ("fresh", .6), ("floral", .4)),
         top_notes=["Lime", "Lemon", "Bergamot", "Calone"], middle_notes=["Sea Notes", "Jasmine", "Rosemary"],
         base_notes=["White Musk", "Cedar", "Patchouli"]),
    dict(name="Cool Water", brand="Davidoff", year=1988, family="Aromatic Aquatic", sentiment=-2, status="tried",
         accords=A(("aquatic", 1), ("aromatic", .8), ("fresh spicy", .6), ("green", .5)),
         top_notes=["Sea Water", "Mint", "Lavender", "Calone"], middle_notes=["Geranium", "Jasmine", "Neroli"],
         base_notes=["Musk", "Cedar", "Ambergris"], perfumers=["Pierre Bourdon"]),
    dict(name="Sauvage", brand="Dior", year=2015, family="Aromatic Fougere", sentiment=0, status="owned",
         accords=A(("fresh spicy", 1), ("amber", .72), ("citrus", .64), ("aromatic", .55)),
         top_notes=["Calabrian bergamot", "Pepper"], middle_notes=["Sichuan Pepper", "Lavender", "Pink Pepper", "Vetiver"],
         base_notes=["Ambroxan", "Cedar", "Labdanum"], perfumers=["François Demachy"]),
    dict(name="Oud Wood", brand="Tom Ford", year=2007, family="Woody", sentiment=1, status="sample",
         accords=A(("woody", 1), ("oud", .8), ("warm spicy", .55), ("amber", .4)),
         top_notes=["Rosewood", "Cardamom", "Chinese Pepper"], middle_notes=["Oud", "Sandalwood", "Vetiver"],
         base_notes=["Tonka Bean", "Vanilla", "Amber"], perfumers=["Richard Herpin"]),
    dict(name="Angels' Share", brand="By Kilian", year=2020, family="Amber Spicy", sentiment=2, status="owned",
         accords=A(("sweet", 1), ("warm spicy", .85), ("vanilla", .7), ("woody", .5)),
         top_notes=["Cognac"], middle_notes=["Cinnamon", "Tonka Bean", "Oak"], base_notes=["Praline", "Vanilla", "Sandalwood"],
         perfumers=["Benoist Lapouza"]),
    dict(name="Light Blue Pour Homme", brand="Dolce & Gabbana", year=2007, family="Citrus Aromatic", sentiment=-1,
         status="owned", accords=A(("citrus", 1), ("aromatic", .7), ("fresh spicy", .6), ("aquatic", .5)),
         top_notes=["Sicilian Mandarin", "Grapefruit", "Bergamot"], middle_notes=["Pepper", "Rosemary", "Rosewood"],
         base_notes=["Musk", "Incense", "Oakmoss"]),
    dict(name="Bleu de Chanel EDP", brand="Chanel", year=2014, family="Woody Aromatic", sentiment=0, status="owned",
         accords=A(("citrus", 1), ("amber", .7), ("woody", .65), ("aromatic", .6)),
         top_notes=["Grapefruit", "Lemon", "Mint", "Pink Pepper"], middle_notes=["Ginger", "Jasmine", "Nutmeg"],
         base_notes=["Incense", "Vetiver", "Cedar", "Sandalwood", "Labdanum"], perfumers=["Jacques Polge"]),
    dict(name="Layton", brand="Parfums de Marly", year=2016, family="Amber Floral", sentiment=None, status="wishlist",
         accords=A(("warm spicy", 1), ("vanilla", .8), ("aromatic", .6), ("sweet", .5)),
         top_notes=["Apple", "Lavender", "Bergamot"], middle_notes=["Geranium", "Violet", "Jasmine"],
         base_notes=["Vanilla", "Cardamom", "Sandalwood", "Pepper", "Patchouli"], perfumers=["Hamid Merati-Kashani"]),
    dict(name="L'Eau d'Issey Pour Homme", brand="Issey Miyake", year=1994, family="Woody Aquatic", sentiment=None,
         status="wishlist", accords=A(("citrus", 1), ("aquatic", .9), ("fresh spicy", .6), ("woody", .5)),
         top_notes=["Yuzu", "Calone", "Lemon"], middle_notes=["Nutmeg", "Water Lily"], base_notes=["Vetiver", "Musk", "Cedar"],
         perfumers=["Jacques Cavallier"]),
]

if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo.db")
    if path.exists():
        path.unlink()
    conn = db.connect(path)
    rng = random.Random(7)
    today = date.today()
    ids = {d["name"]: db.insert_fragrance(conn, {**d, "source": "manual"}) for d in DEMO}
    rotation = {"Tobacco Vanille": 14, "Grand Soir": 10, "Angels' Share": 9, "Sauvage": 6, "Baccarat Rouge 540": 5,
                "Bleu de Chanel EDP": 4, "Oud Wood": 2}
    for name, n in rotation.items():
        for _ in range(n):
            worn = today - timedelta(days=rng.randint(0, 330))
            cold = worn.month in (11, 12, 1, 2)
            db.insert_wear(conn, {
                "fragrance_id": ids[name], "worn_on": worn,
                "occasion": rng.choice(["Work", "Date", "Night out", "Casual"]),
                "weather": "Cold" if cold else rng.choice(["Mild", "Warm"]),
                "sprays": rng.randint(2, 5), "enjoyment": rng.choice([1, 2]) if name != "Sauvage" else 0,
                "compliments": rng.choice([0, 0, 1, 2]), "notes": None,
            })
    print(f"Demo data written to {path} ({len(DEMO)} fragrances)")
