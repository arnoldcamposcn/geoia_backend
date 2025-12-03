from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime
from bson import ObjectId
import pandas as pd
import os

from backend.corelab.drilldata import DrillData
from backend.corelab.engine import CoreLabEngine
from backend.auth import router as auth_router, get_current_user, UserPublic
from backend.database import db

app = FastAPI(title="CoreLab 3D API")

# Rutas de autenticación
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# DIRECTORIO UPLOADS
# -------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Colección de proyectos 3D en Mongo
projects_collection = db["render_3d"]

# -------------------------------------------------------
# ESTADO EN MEMORIA POR PROYECTO
# -------------------------------------------------------
# current_state[project_id] = {
#   "files": {...},
#   "columns": {...},
#   "drillholes": [...],
#   "assay_variables": [...],
#   "assay_feature": str | None,
#   "assay_render": [...],
#   "project_id": str,
#   "composites": {...},
#   "composite_settings": {...},
#   "block_model": [...],
#   "block_settings": {...},
#   "surface_dxf": [...]
# }
current_state: Dict[str, Dict[str, Any]] = {}


def get_user_folder(user_id: str) -> str:
    """Crea (si no existe) y devuelve la carpeta para ese usuario."""
    folder = os.path.join(UPLOAD_DIR, user_id)
    os.makedirs(folder, exist_ok=True)
    return folder


# =======================================================
# 1) SUBIR ARCHIVOS + OBTENER CABECERAS (CON AUTENTICACIÓN)
#    Crea SIEMPRE un proyecto nuevo
# =======================================================
@app.post("/upload-files")
async def upload_files(
    collar: UploadFile = File(...),
    survey: UploadFile = File(...),
    lith: UploadFile = File(...),
    assay: UploadFile = File(...),
    dxf: UploadFile | None = File(None),
    nombre_proyecto: str = Form(...),
    current_user: UserPublic = Depends(get_current_user),
):
    # Carpeta por usuario (para guardar CSVs físicos)
    user_folder = get_user_folder(current_user.id)

    # Paths CSV
    collar_path = os.path.join(user_folder, "collar_raw.csv")
    survey_path = os.path.join(user_folder, "survey_raw.csv")
    lith_path = os.path.join(user_folder, "lith_raw.csv")
    assay_path = os.path.join(user_folder, "assay_raw.csv")

    # Guardar archivos originales CSV
    with open(collar_path, "wb") as f:
        f.write(await collar.read())
    with open(survey_path, "wb") as f:
        f.write(await survey.read())
    with open(lith_path, "wb") as f:
        f.write(await lith.read())
    with open(assay_path, "wb") as f:
        f.write(await assay.read())

    # ------------------------------------
    #   Guardar DXF si viene
    # ------------------------------------
    dxf_path = None
    surface_points = None
    dxf_status = None

    if dxf is not None:
        dxf_path = os.path.join(user_folder, "surface.dxf")
        with open(dxf_path, "wb") as f:
            f.write(await dxf.read())

        engine = CoreLabEngine()
        surface_points = engine.load_dxf_surface(dxf_path)

        dxf_status = {
            "ok": True,
            "points": len(surface_points),
            "message": "DXF cargado y procesado correctamente.",
        }
    else:
        engine = CoreLabEngine()

    # Leer headers CSV
    collar_df = pd.read_csv(collar_path)
    survey_df = pd.read_csv(survey_path)
    lith_df = pd.read_csv(lith_path)
    assay_df = pd.read_csv(assay_path)

    # ============================================
    # 1) DETECCIÓN DE MAPEOS AUTOMÁTICOS
    # ============================================
    def find(col_list, expected):
        for e in expected:
            for c in col_list:
                if e.lower() == c.lower():
                    return c
        return None

    detected = {
        "collar": {
            "ID": find(collar_df.columns, ["ID", "BHID", "HOLEID"]),
            "X": find(collar_df.columns, ["X", "XCOLLAR", "EASTING"]),
            "Y": find(collar_df.columns, ["Y", "YCOLLAR", "NORTHING"]),
            "Z": find(collar_df.columns, ["Z", "ZCOLLAR", "RL", "ELEV"]),
        },
        "survey": {
            "ID": find(survey_df.columns, ["ID", "BHID", "HOLEID"]),
            "AT": find(survey_df.columns, ["AT", "DEPTH"]),
            "AZ": find(survey_df.columns, ["AZ", "AZIMUTH", "BRG"]),
            "DIP": find(survey_df.columns, ["DIP", "INCLINATION"]),
        },
        "lith": {
            "ID": find(lith_df.columns, ["ID", "BHID", "HOLEID"]),
            "FROM": find(lith_df.columns, ["FROM", "FROM_DEPTH"]),
            "TO": find(lith_df.columns, ["TO", "TO_DEPTH"]),
            "ROCK": find(lith_df.columns, ["ROCK", "LITH", "LITHOLOGY"]),
        },
        "assay": {
            "ID": find(assay_df.columns, ["ID", "BHID", "HOLEID"]),
            "FROM": find(assay_df.columns, ["FROM", "FROM_DEPTH"]),
            "TO": find(assay_df.columns, ["TO", "TO_DEPTH"]),
        },
    }

    # ============================================
    # 2) RENOMBRAR A FORMATO ESTÁNDAR
    # ============================================
    collar_std = collar_df.rename(
        columns={
            detected["collar"]["ID"]: "ID",
            detected["collar"]["X"]: "X",
            detected["collar"]["Y"]: "Y",
            detected["collar"]["Z"]: "Z",
        }
    )
    collar_std_path = os.path.join(user_folder, "collar_std.csv")
    collar_std.to_csv(collar_std_path, index=False)

    survey_std = survey_df.rename(
        columns={
            detected["survey"]["ID"]: "ID",
            detected["survey"]["AT"]: "AT",
            detected["survey"]["AZ"]: "AZ",
            detected["survey"]["DIP"]: "DIP",
        }
    )
    survey_std_path = os.path.join(user_folder, "survey_std.csv")
    survey_std.to_csv(survey_std_path, index=False)

    lith_std = lith_df.rename(
        columns={
            detected["lith"]["ID"]: "ID",
            detected["lith"]["FROM"]: "FROM",
            detected["lith"]["TO"]: "TO",
            detected["lith"]["ROCK"]: "ROCK",
        }
    )
    lith_std_path = os.path.join(user_folder, "lith_std.csv")
    lith_std.to_csv(lith_std_path, index=False)

    assay_std = assay_df.rename(
        columns={
            detected["assay"]["ID"]: "ID",
            detected["assay"]["FROM"]: "FROM",
            detected["assay"]["TO"]: "TO",
        }
    )
    assay_std_path = os.path.join(user_folder, "assay_std.csv")
    assay_std.to_csv(assay_std_path, index=False)

    # ============================================
    # 3) Construir Drillholes
    # ============================================
    drilldata = DrillData(
        collar_file=collar_std_path,
        survey_file=survey_std_path,
        lith_file=lith_std_path,
        collar_map={"ID": "ID", "X": "X", "Y": "Y", "Z": "Z"},
        survey_map={"ID": "ID", "AT": "AT", "AZ": "AZ", "DIP": "DIP"},
        lith_map={"ID": "ID", "FROM": "FROM", "TO": "TO", "ROCK": "ROCK"},
    )

    drillholes = engine.build_drillhole_model(drilldata)

    # ============================================
    # 4) Guardar estado en memoria y en Mongo
    # ============================================
    files_state = {
        "collar_path": collar_std_path,
        "survey_path": survey_std_path,
        "lith_path": lith_std_path,
        "assay_path": assay_std_path,
    }
    if dxf_path is not None:
        files_state["dxf_path"] = dxf_path

    state: Dict[str, Any] = {
        "files": files_state,
        "nombre_proyecto": nombre_proyecto,
        "columns": detected,
        "drillholes": drillholes,
        "assay_variables": assay_std.select_dtypes(include=["number"]).columns.tolist(),
    }

    if surface_points is not None:
        state["surface_dxf"] = surface_points

    # ---- Guardar proyecto en MongoDB ----
    project_doc: Dict[str, Any] = {
        "user_id": ObjectId(current_user.id),
        "nombre_proyecto": nombre_proyecto,
        "created_at": datetime.utcnow(),
        "files": files_state,
        "columns": detected,
        "drillholes": drillholes,
        "assay_variables": state["assay_variables"],
    }
    if surface_points is not None:
        project_doc["surface_dxf"] = surface_points

    result = projects_collection.insert_one(project_doc)
    project_id = str(result.inserted_id)

    state["project_id"] = project_id
    current_state[project_id] = state

    # ============================================
    # 5) Respuesta
    # ============================================
    return {
        "ok": True,
        "project_id": project_id,
        "mapping_detected": detected,
        "collar_headers": list(collar_df.columns),
        "survey_headers": list(survey_df.columns),
        "lith_headers": list(lith_df.columns),
        "assay_headers": list(assay_df.columns),
        "total_holes": len(drillholes),
        "dxf_status": dxf_status,
    }


# =======================================================
# GET SURFACE POINTS (por proyecto)
# =======================================================
@app.get("/surface")
def get_surface(project_id: str):
    state = current_state.get(project_id, {})

    surface = state.get("surface_dxf")
    if surface is None:
        return {"ok": False, "error": "No hay DXF cargado para ese proyecto."}

    return {"ok": True, "surface": surface}


# =======================================================
# 2) SCHEMAS
# =======================================================
class CollarMap(BaseModel):
    ID: str
    X: str
    Y: str
    Z: str


class SurveyMap(BaseModel):
    ID: str
    AT: str
    AZ: str
    DIP: str


class LithMap(BaseModel):
    ID: str
    FROM: str
    TO: str
    ROCK: str


class ColumnSelection(BaseModel):
    collar: CollarMap
    survey: SurveyMap
    lith: LithMap


class BlockModelRequest(BaseModel):
    composite_name: str
    block_size_x: float
    block_size_y: float
    block_size_z: float
    padding: float = 20  # padding recomendado


class FeatureRequest(BaseModel):
    feature: str


class CompositeRequest(BaseModel):
    variable: str
    composite_length: float
    top_cut: float | None = None
    min_samples: int = 1


# =======================================================
# 3) GET DRILLHOLES (Render clásico por proyecto)
# =======================================================
@app.get("/drillholes")
def get_drillholes(project_id: str):
    state = current_state.get(project_id)

    if not state or state.get("drillholes") is None:
        return {"ok": False, "error": "No se ha generado modelo para ese proyecto."}

    return {"ok": True, "drillholes": state["drillholes"]}


# =======================================================
# 4) Seleccionar variable numérica para Assay (SOLO MEMORIA)
# =======================================================
@app.post("/assay-feature")
def assay_feature(req: FeatureRequest, project_id: str):
    state = current_state.get(project_id)

    if not state or state.get("drillholes") is None:
        return {
            "ok": False,
            "error": "Primero genera el modelo (sube archivos) para ese proyecto.",
        }

    assay_path = state["files"]["assay_path"]
    assay_df = pd.read_csv(assay_path)

    if req.feature not in assay_df.columns:
        return {"ok": False, "error": f"Variable '{req.feature}' no existe en assay.csv"}

    engine = CoreLabEngine()
    assay_render = engine.build_render(state["drillholes"], req.feature, assay_path)

    state["assay_feature"] = req.feature
    state["assay_render"] = assay_render

    return {
        "ok": True,
        "feature": req.feature,
        "message": "Render generado en memoria (no persistido en BD).",
    }


# =======================================================
# GET COMPOSITES
# =======================================================
@app.get("/composites")
def get_composites(project_id: str):

    state = current_state.get(project_id)

    if state is None:
        return {
            "ok": False,
            "error": "No se ha generado modelo. Sube archivos para ese proyecto.",
        }

    if state.get("composites") is None:
        return {"ok": False, "error": "No existen compositos. Usa POST /composites."}

    return {
        "ok": True,
        "settings": state.get("composite_settings"),
        "composites": state.get("composites"),
    }


# =======================================================
# POST COMPOSITES SETTINGS (Puntos coloreados, memoria + BD)
# =======================================================
@app.post("/composites")
def generate_composites(req: CompositeRequest, project_id: str):

    state = current_state.get(project_id)

    # Si no está en memoria, intentar restaurar desde MongoDB
    if not state:
        try:
            oid = ObjectId(project_id)
        except Exception:
            return {"ok": False, "error": "project_id inválido."}

        doc = projects_collection.find_one({"_id": oid})
        if not doc:
            return {"ok": False, "error": "Proyecto no encontrado."}

        # Restaurar estado en memoria
        current_state[project_id] = {
            "files": doc.get("files", {}),
            "nombre_proyecto": doc.get("nombre_proyecto", ""),
            "columns": doc.get("columns", {}),
            "drillholes": doc.get("drillholes", []),
            "assay_variables": doc.get("assay_variables", []),
            "project_id": project_id,
            "composites": doc.get("composites", {}),
            "composite_settings": doc.get("composite_settings", {}),
            "block_model": doc.get("block_model", []),
            "block_settings": doc.get("block_settings", {}),
            "surface_dxf": doc.get("surface_dxf", []),
        }
        state = current_state[project_id]

    if not state or state.get("drillholes") is None:
        return {
            "ok": False,
            "error": "Primero genera modelo para ese proyecto (sube archivos).",
        }

    assay_path = state["files"]["assay_path"]
    assay_df = pd.read_csv(assay_path)

    if req.variable not in assay_df.columns:
        return {
            "ok": False,
            "error": f"La variable '{req.variable}' no existe en assay.csv",
        }

    engine = CoreLabEngine()

    # -------- GENERA EL NOMBRE DEL COMPOSITE ----------------------
    name_parts = [req.variable, f"comp_{req.composite_length}m"]
    if req.top_cut is not None:
        name_parts.append(f"tc{req.top_cut}")
    comp_name = "_".join(str(x) for x in name_parts)

    # -------- GENERA EL COMPOSITE ----------------------
    comps = engine.build_composites(
        drillholes=state["drillholes"],
        assay_df=assay_df,
        variable=req.variable,
        length=req.composite_length,
        top_cut=req.top_cut,
        min_samples=req.min_samples,
    )

    # -------- REEMPLAZAR NA → -99 ----------------------
    for c in comps:
        if c["value"] is None or c["value"] != c["value"]:  # NaN
            c["value"] = -99
        if c["value"] < 0:
            c["value"] = -99

    # -------- GUARDAR EN MEMORIA ----------------------
        # -------- GUARDAR EN MEMORIA ----------------------
    if state.get("composites") is None:
        state["composites"] = {}

    if state.get("composite_settings") is None:
        state["composite_settings"] = {}


    # state["composites"][comp_name] = comps
    # state["composite_settings"][comp_name] = req.model_dump()

    # Reemplazar todos los composites existentes con el nuevo
    state["composites"] = {comp_name: comps}
    state["composite_settings"] = {comp_name: req.model_dump()}

    # -------- GUARDAR EN MONGO ------------------------
    projects_collection.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$set": {
                "composites": state["composites"],
                "composite_settings": state["composite_settings"],
                "updated_at": datetime.utcnow(),
            }
        },
    )

    return {
        "ok": True,
        "name": comp_name,
        "total": len(comps),
        "available_composites": list(state["composites"].keys()),
    }


@app.get("/composites/list")
def list_composites(project_id: str):
    state = current_state.get(project_id, {})
    comps = state.get("composites", {})

    return {
        "ok": True,
        "available": list(comps.keys()),
    }


# =======================================================
# GET BLOCK MODEL
# =======================================================
@app.get("/block-model")
def get_block_model(project_id: str):

    state = current_state.get(project_id)

    if not state or state.get("block_model") is None:
        return {"ok": False, "error": "No hay block model generado para ese proyecto."}

    return {"ok": True, "settings": state["block_settings"], "model": state["block_model"]}


# =======================================================
# POST BLOCK MODEL
# =======================================================
@app.post("/block-model")
def generate_block_model(req: BlockModelRequest, project_id: str):

    state = current_state.get(project_id)

    if not state:
        return {
            "ok": False,
            "error": "No hay estado para ese proyecto. Sube archivos primero.",
        }

    if "composites" not in state:
        return {"ok": False, "error": "Primero genera composites."}

    # ---------------- VALIDAR NOMBRE DE COMPOSITE ----------------
    if req.composite_name not in state["composites"]:
        return {
            "ok": False,
            "error": f"Composite '{req.composite_name}' no existe.",
            "available": list(state["composites"].keys()),
        }

    # Cargar composite seleccionado
    comps = state["composites"][req.composite_name]

    # ---------------- BOUNDING BOX AUTOMÁTICO ----------------
    engine = CoreLabEngine()
    minX, maxX, minY, maxY, minZ, maxZ = engine.auto_detect_bbox(
        drillholes=state["drillholes"],
        surface_points=state.get("surface_dxf"),
        padding=req.padding,
    )

    # ---------------- CONSTRUIR BLOQUES ----------------
    blocks = []
    x = minX
    while x < maxX:
        y = minY
        while y < maxY:
            z = minZ
            while z < maxZ:
                blocks.append(
                    {
                        "x": x + req.block_size_x / 2,
                        "y": y + req.block_size_y / 2,
                        "z": z + req.block_size_z / 2,
                        "size_x": req.block_size_x,
                        "size_y": req.block_size_y,
                        "size_z": req.block_size_z,
                        req.composite_name + "_est": -99,
                    }
                )
                z += req.block_size_z
            y += req.block_size_y
        x += req.block_size_x

    # if len(blocks) > 9_000_000:
    #     return {
    #         "ok": False,
    #         "error": "Demasiados bloques. Reduce tamaño o padding.",
    #     }

    # ---------------- KRIGING ----------------
    engine = CoreLabEngine()

    samples = comps
    est_field = req.composite_name + "_est"
    blocks = engine.krige_blocks(samples, blocks, variable="value")

    for b in blocks:
        val = b.get("value", None)

        if val is None or val != val or val < 0:
            b[est_field] = -99
        else:
            b[est_field] = float(val)

        del b["value"]

    state["block_model"] = blocks
    state["block_settings"] = {
        "using_composite": req.composite_name,
        "block_size_x": req.block_size_x,
        "block_size_y": req.block_size_y,
        "block_size_z": req.block_size_z,
        "padding": req.padding,
        "auto_bbox": {
            "minX": minX,
            "maxX": maxX,
            "minY": minY,
            "maxY": maxY,
            "minZ": minZ,
            "maxZ": maxZ,
        },
    }

    # Guardar en Mongo
    projects_collection.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$set": {
                "block_model": state["block_model"],
                "block_settings": state["block_settings"],
                "updated_at": datetime.utcnow(),
            }
        },
    )

    return {"ok": True, "settings": state["block_settings"], "model": blocks}


# =======================================================
# 5) GET ASSAY RENDER (Puntos coloreados, sólo memoria)
# =======================================================
@app.get("/assay-render")
def get_assay_render(project_id: str):
    state = current_state.get(project_id)

    if not state or state.get("assay_render") is None:
        return {
            "ok": False,
            "error": "No hay render generado. Usa /assay-feature para ese proyecto.",
        }

    return {
        "ok": True,
        "feature": state["assay_feature"],
        "render": state["assay_render"],
    }


# =======================================================
# 6) LISTAR PROYECTOS GUARDADOS DEL USUARIO
# =======================================================
@app.get("/projects")
def list_projects(current_user: UserPublic = Depends(get_current_user)):
    cursor = projects_collection.find(
        {"user_id": ObjectId(current_user.id)},
        {"drillholes": 0},  # puedes ocultar/mostrar según convenga
    )

    projects = []
    for doc in cursor:
        projects.append(
            {
                "id": str(doc["_id"]),
                "nombre_proyecto": doc.get("nombre_proyecto", ""),
                "created_at": doc.get("created_at"),
                "has_drillholes": "drillholes" in doc,
            }
        )

    return {"ok": True, "projects": projects}


# =======================================================
# 7) OBTENER UN PROYECTO COMPLETO POR ID
#    y restaurar estado en memoria
# =======================================================
@app.get("/projects/{project_id}")
def get_project(
    project_id: str,
    current_user: UserPublic = Depends(get_current_user),
):
    try:
        oid = ObjectId(project_id)
    except Exception:
        return {"ok": False, "error": "project_id inválido."}

    doc = projects_collection.find_one(
        {"_id": oid, "user_id": ObjectId(current_user.id)}
    )

    if not doc:
        return {"ok": False, "error": "Proyecto no encontrado."}

    # Restaurar estado en memoria para ese proyecto
    current_state[project_id] = {
        "files": doc.get("files", {}),
        "nombre_proyecto": doc.get("nombre_proyecto", ""),
        "columns": doc.get("columns", {}),
        "drillholes": doc.get("drillholes", []),
        "assay_variables": doc.get("assay_variables", []),
        "assay_feature": None,
        "assay_render": None,
        "project_id": project_id,
        "composites": doc.get("composites"),
        "composite_settings": doc.get("composite_settings"),
        "block_model": doc.get("block_model"),
        "block_settings": doc.get("block_settings"),
        "surface_dxf": doc.get("surface_dxf"),
    }

    response_doc = {
        "id": str(doc["_id"]),
        "user_id": str(doc["user_id"]),
        "nombre_proyecto": doc.get("nombre_proyecto", ""),
        "created_at": doc.get("created_at"),
        "files": doc.get("files", {}),
        "columns": doc.get("columns", {}),
        "drillholes": doc.get("drillholes", []),
        "assay_variables": doc.get("assay_variables", []),
        "surface": doc.get("surface_dxf", []),
        "composites": doc.get("composites", []),
        "block_model": doc.get("block_model", []),
        "assay_variables": doc.get("assay_variables",[])
    }

    return {"ok": True, "project": response_doc}


# =======================================================
# 8) EDITAR UN PROYECTO POR ID (PATCH)
# =======================================================
@app.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    nombre_proyecto: Optional[str] = Form(None),
    collar: Optional[UploadFile] = File(None),
    survey: Optional[UploadFile] = File(None),
    lith: Optional[UploadFile] = File(None),
    assay: Optional[UploadFile] = File(None),
    dxf: Optional[UploadFile] = File(None),
    current_user: UserPublic = Depends(get_current_user),
):
    """
    Actualiza un proyecto existente.
    Permite actualizar el nombre y/o reemplazar archivos.
    Si se actualizan archivos base (collar, survey, lith, assay), se regenera toda la información.
    """
    try:
        oid = ObjectId(project_id)
    except Exception:
        return {"ok": False, "error": "project_id inválido."}

    # Verificar que el proyecto exista y pertenezca al usuario
    doc = projects_collection.find_one(
        {"_id": oid, "user_id": ObjectId(current_user.id)}
    )

    if not doc:
        return {"ok": False, "error": "Proyecto no encontrado."}

    # Restaurar estado en memoria si no está
    if project_id not in current_state:
        current_state[project_id] = {
            "files": doc.get("files", {}),
            "nombre_proyecto": doc.get("nombre_proyecto", ""),
            "columns": doc.get("columns", {}),
            "drillholes": doc.get("drillholes", []),
            "assay_variables": doc.get("assay_variables", []),
            "project_id": project_id,
            "composites": doc.get("composites"),
            "composite_settings": doc.get("composite_settings"),
            "block_model": doc.get("block_model"),
            "block_settings": doc.get("block_settings"),
            "surface_dxf": doc.get("surface_dxf"),
        }

    state = current_state[project_id]
    user_folder = get_user_folder(current_user.id)
    files_updated = False
    files_state = state["files"].copy()

    # Actualizar nombre del proyecto si se proporciona
    if nombre_proyecto is not None:
        state["nombre_proyecto"] = nombre_proyecto

    # Procesar archivos actualizados
    # Si se actualiza collar, survey, lith o assay, necesitamos regenerar todo
    base_files_updated = any([collar, survey, lith, assay])

    # Actualizar archivos si se proporcionan
    if collar:
        collar_path = os.path.join(user_folder, f"collar_raw_{project_id}.csv")
        with open(collar_path, "wb") as f:
            f.write(await collar.read())
        files_state["collar_path"] = collar_path
        files_updated = True

    if survey:
        survey_path = os.path.join(user_folder, f"survey_raw_{project_id}.csv")
        with open(survey_path, "wb") as f:
            f.write(await survey.read())
        files_state["survey_path"] = survey_path
        files_updated = True

    if lith:
        lith_path = os.path.join(user_folder, f"lith_raw_{project_id}.csv")
        with open(lith_path, "wb") as f:
            f.write(await lith.read())
        files_state["lith_path"] = lith_path
        files_updated = True

    if assay:
        assay_path = os.path.join(user_folder, f"assay_raw_{project_id}.csv")
        with open(assay_path, "wb") as f:
            f.write(await assay.read())
        files_state["assay_path"] = assay_path
        files_updated = True

    if dxf:
        dxf_path = os.path.join(user_folder, f"surface_{project_id}.dxf")
        with open(dxf_path, "wb") as f:
            f.write(await dxf.read())
        files_state["dxf_path"] = dxf_path
        files_updated = True

    # Procesar DXF si se actualizó (independientemente de si se actualizaron archivos base)
    if dxf:
        engine = CoreLabEngine()
        surface_points = engine.load_dxf_surface(dxf_path)
        state["surface_dxf"] = surface_points

    # Si se actualizaron archivos base, regenerar toda la información
    if base_files_updated:
        # Leer archivos actualizados o existentes
        collar_path = files_state.get("collar_path")
        survey_path = files_state.get("survey_path")
        lith_path = files_state.get("lith_path")
        assay_path = files_state.get("assay_path")

        if not all([collar_path, survey_path, lith_path, assay_path]):
            return {
                "ok": False,
                "error": "Faltan archivos base (collar, survey, lith, assay) para regenerar el proyecto."
            }

        # Leer headers CSV
        collar_df = pd.read_csv(collar_path)
        survey_df = pd.read_csv(survey_path)
        lith_df = pd.read_csv(lith_path)
        assay_df = pd.read_csv(assay_path)

        # Detección de mapeos automáticos (misma lógica que upload-files)
        def find(col_list, expected):
            for e in expected:
                for c in col_list:
                    if e.lower() == c.lower():
                        return c
            return None

        detected = {
            "collar": {
                "ID": find(collar_df.columns, ["ID", "BHID", "HOLEID"]),
                "X": find(collar_df.columns, ["X", "XCOLLAR", "EASTING"]),
                "Y": find(collar_df.columns, ["Y", "YCOLLAR", "NORTHING"]),
                "Z": find(collar_df.columns, ["Z", "ZCOLLAR", "RL", "ELEV"]),
            },
            "survey": {
                "ID": find(survey_df.columns, ["ID", "BHID", "HOLEID"]),
                "AT": find(survey_df.columns, ["AT", "DEPTH"]),
                "AZ": find(survey_df.columns, ["AZ", "AZIMUTH", "BRG"]),
                "DIP": find(survey_df.columns, ["DIP", "INCLINATION"]),
            },
            "lith": {
                "ID": find(lith_df.columns, ["ID", "BHID", "HOLEID"]),
                "FROM": find(lith_df.columns, ["FROM", "FROM_DEPTH"]),
                "TO": find(lith_df.columns, ["TO", "TO_DEPTH"]),
                "ROCK": find(lith_df.columns, ["ROCK", "LITH", "LITHOLOGY"]),
            },
            "assay": {
                "ID": find(assay_df.columns, ["ID", "BHID", "HOLEID"]),
                "FROM": find(assay_df.columns, ["FROM", "FROM_DEPTH"]),
                "TO": find(assay_df.columns, ["TO", "TO_DEPTH"]),
            },
        }

        # Renombrar a formato estándar
        collar_std = collar_df.rename(
            columns={
                detected["collar"]["ID"]: "ID",
                detected["collar"]["X"]: "X",
                detected["collar"]["Y"]: "Y",
                detected["collar"]["Z"]: "Z",
            }
        )
        collar_std_path = os.path.join(user_folder, f"collar_std_{project_id}.csv")
        collar_std.to_csv(collar_std_path, index=False)

        survey_std = survey_df.rename(
            columns={
                detected["survey"]["ID"]: "ID",
                detected["survey"]["AT"]: "AT",
                detected["survey"]["AZ"]: "AZ",
                detected["survey"]["DIP"]: "DIP",
            }
        )
        survey_std_path = os.path.join(user_folder, f"survey_std_{project_id}.csv")
        survey_std.to_csv(survey_std_path, index=False)

        lith_std = lith_df.rename(
            columns={
                detected["lith"]["ID"]: "ID",
                detected["lith"]["FROM"]: "FROM",
                detected["lith"]["TO"]: "TO",
                detected["lith"]["ROCK"]: "ROCK",
            }
        )
        lith_std_path = os.path.join(user_folder, f"lith_std_{project_id}.csv")
        lith_std.to_csv(lith_std_path, index=False)

        assay_std = assay_df.rename(
            columns={
                detected["assay"]["ID"]: "ID",
                detected["assay"]["FROM"]: "FROM",
                detected["assay"]["TO"]: "TO",
            }
        )
        assay_std_path = os.path.join(user_folder, f"assay_std_{project_id}.csv")
        assay_std.to_csv(assay_std_path, index=False)

        # Actualizar paths en files_state
        files_state["collar_path"] = collar_std_path
        files_state["survey_path"] = survey_std_path
        files_state["lith_path"] = lith_std_path
        files_state["assay_path"] = assay_std_path

        # Construir Drillholes
        drilldata = DrillData(
            collar_file=collar_std_path,
            survey_file=survey_std_path,
            lith_file=lith_std_path,
            collar_map={"ID": "ID", "X": "X", "Y": "Y", "Z": "Z"},
            survey_map={"ID": "ID", "AT": "AT", "AZ": "AZ", "DIP": "DIP"},
            lith_map={"ID": "ID", "FROM": "FROM", "TO": "TO", "ROCK": "ROCK"},
        )

        engine = CoreLabEngine()
        drillholes = engine.build_drillhole_model(drilldata)

        # Actualizar estado
        state["files"] = files_state
        state["columns"] = detected
        state["drillholes"] = drillholes
        state["assay_variables"] = assay_std.select_dtypes(include=["number"]).columns.tolist()
        
        # Limpiar datos derivados que necesitan regenerarse
        state["composites"] = None
        state["composite_settings"] = None
        state["block_model"] = None
        state["block_settings"] = None
        state["assay_feature"] = None
        state["assay_render"] = None

        # Procesar DXF si existe pero no se actualizó en este request
        if not dxf:
            dxf_path = files_state.get("dxf_path")
            if dxf_path and os.path.exists(dxf_path):
                surface_points = engine.load_dxf_surface(dxf_path)
                state["surface_dxf"] = surface_points

    # Actualizar MongoDB
    update_doc = {
        "updated_at": datetime.utcnow(),
    }

    if nombre_proyecto is not None:
        update_doc["nombre_proyecto"] = nombre_proyecto

    if files_updated:
        update_doc["files"] = files_state

    if base_files_updated:
        update_doc.update({
            "columns": state["columns"],
            "drillholes": state["drillholes"],
            "assay_variables": state["assay_variables"],
            "composites": None,
            "composite_settings": None,
            "block_model": None,
            "block_settings": None,
        })
        if state.get("surface_dxf"):
            update_doc["surface_dxf"] = state["surface_dxf"]
    
    # Si solo se actualizó el DXF, actualizar surface_dxf en MongoDB
    if dxf and not base_files_updated:
        if state.get("surface_dxf"):
            update_doc["surface_dxf"] = state["surface_dxf"]

    projects_collection.update_one(
        {"_id": oid},
        {"$set": update_doc}
    )

    return {
        "ok": True,
        "message": "Proyecto actualizado correctamente",
        "project_id": project_id,
        "files_updated": files_updated,
        "base_files_updated": base_files_updated,
        "regenerated": base_files_updated,
    }


# =======================================================
# 9) ELIMINAR UN PROYECTO POR ID
# =======================================================
@app.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    current_user: UserPublic = Depends(get_current_user),
):
    try:
        oid = ObjectId(project_id)
    except Exception:
        return {"ok": False, "error": "project_id inválido."}

    # Verificar que el proyecto exista y pertenezca al usuario
    doc = projects_collection.find_one(
        {"_id": oid, "user_id": ObjectId(current_user.id)}
    )

    if not doc:
        return {"ok": False, "error": "Proyecto no encontrado."}

    # Eliminar archivos físicos si existen
    files = doc.get("files", {})
    try:
        if files.get("collar_path") and os.path.exists(files["collar_path"]):
            os.remove(files["collar_path"])
        if files.get("survey_path") and os.path.exists(files["survey_path"]):
            os.remove(files["survey_path"])
        if files.get("lith_path") and os.path.exists(files["lith_path"]):
            os.remove(files["lith_path"])
        if files.get("assay_path") and os.path.exists(files["assay_path"]):
            os.remove(files["assay_path"])
        if files.get("dxf_path") and os.path.exists(files["dxf_path"]):
            os.remove(files["dxf_path"])
    except Exception as e:
        print(f"Error eliminando archivos: {e}")

    # Eliminar de MongoDB
    result = projects_collection.delete_one({"_id": oid})

    # Eliminar del estado en memoria
    if project_id in current_state:
        del current_state[project_id]

    if result.deleted_count > 0:
        return {"ok": True, "message": "Proyecto eliminado correctamente"}
    else:
        return {"ok": False, "error": "No se pudo eliminar el proyecto"}
