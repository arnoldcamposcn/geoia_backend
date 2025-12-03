import math
import pandas as pd
import numpy as np
# from pykrige.ok import OrdinaryKriging
from scipy.spatial import cKDTree
import ezdxf

class CoreLabEngine:

    """
    Motor central de cálculo del sistema CoreLab 3D.

    Incluye:
    - Construcción de trayectorias 3D desde azimut y buzamiento
    - Construcción del modelo clásico de sondajes (trajectory + lithology)
    - Generación de compositos (compositing regular por longitud)
    - Render numérico continuo basado en intervals de assay
    - Construcción de samples 3D reales (interpolando la trayectoria)
    - Kriging local (variograma esférico, rango, sill, nugget)
    - Lectura de superficies desde archivos DXF

    Este motor NO depende del estado del backend, es una clase pura
    donde cada función recibe datos y devuelve datos.
    """

    # ---------------------------------------------------------
    # 1) CÁLCULO DE TRAYECTORIA
    # ---------------------------------------------------------
    def compute_trajectory(self, collar_row, survey_df):

        """
        Calcula la trayectoria 3D de un sondaje usando azimut (AZ) y dip (DIP)
        con método incremental.

        Matemática:
            Para un intervalo de longitud L:
                dx = L * sin(AZ) * cos(DIP)
                dy = L * cos(AZ) * cos(DIP)
                dz = L * sin(DIP)

        Notas:
            - El Z disminuye porque la profundidad crece hacia abajo.
            - DIP positivo = inclinación hacia abajo.
            - AZ en grados → convertido a radianes.

        Args:
            collar_row (Series): Fila con X, Y, Z inicial.
            survey_df (DataFrame): Registros AT/AZ/DIP ordenados por profundidad.

        Returns:
            list[(x, y, z, depth)]: Puntos discretos de la trayectoria.
        """


        # Coordenadas iniciales del collar
        x = collar_row["X"]
        y = collar_row["Y"]
        z = collar_row["Z"]

        trajectory = []
        trajectory.append((x, y, z, 0))
        last_depth = 0

        # Recorrer station por station
        for _, row in survey_df.iterrows():

            depth = row["AT"]
            dip = math.radians(row["DIP"])
            azm = math.radians(row["AZ"])

            # Longitud incremental entre estaciones
            advance = depth - last_depth
            last_depth = depth

            # Fórmulas direccionales
            dx = advance * math.sin(azm) * math.cos(dip)
            dy = advance * math.cos(azm) * math.cos(dip)
            dz = advance * math.sin(dip)

            # Actualizar posición
            x += dx
            y += dy
            z -= abs(dz) # produndiza hacia abajo

            trajectory.append((x, y, z, depth))

        return trajectory


    # ---------------------------------------------------------
    # 2) CONSTRUIR MODELO 3D CLÁSICO
    # ---------------------------------------------------------
    def build_drillhole_model(self, drilldata):

        """
        Construye un modelo clásico de sondajes consistente en:
        - trayectoria 3D
        - intervalos de litología

        Args:
            drilldata (DrillData)

        Returns:
            list[dict]: Lista de sondajes estructurados.
        """
        holes = []
        # Recorrer cada BHID/ID único
        for hole_id in drilldata.collar["ID"].unique():

            collar = drilldata.get_collar(hole_id)
            survey = drilldata.get_survey(hole_id)
            lith = drilldata.get_lith(hole_id)
            
            # 3D path
            trajectory = self.compute_trajectory(collar, survey)

            # Litologías
            lith_intervals = []
            for _, row in lith.iterrows():
                lith_intervals.append({
                    "from": float(row["FROM"]),
                    "to": float(row["TO"]),
                    "rock": row["ROCK"]
                })

            # Empaquetar todo
            holes.append({
                "hole_id": hole_id,
                "trajectory": [
                    {"x": p[0], "y": p[1], "z": p[2], "depth": p[3]}
                    for p in trajectory
                ],
                "lithology": lith_intervals
            })

        return holes

    # ---------------------------------------------------------
    # 3) GENERAR COMPOSITES
    # ---------------------------------------------------------
    def build_composites(self, drillholes, assay_df, variable, length, top_cut=None, min_samples=1):
        """
        Genera compositos regulares de longitud fija.

        Lógica:
            - Para cada intervalo [comp_from, comp_to], busca intersecciones
              con los intervalos reales del assay.
            - Calcula promedio ponderado por longitud.
            - Toma la coordenada XYZ del punto de trayectoría más cercano
              al mid-depth del composito.

        Args:
            drillholes (list)
            assay_df (DataFrame)
            variable (str): Nombre de columna numérica del assay.
            length (float): Longitud del composito.
            top_cut (float|None): Límite superior opcional.
            min_samples (int): Mínimo de intervalos para validar.

        Returns:
            list[dict]: Compositos 3D.
        """

        composites = []

        for hole in drillholes:
            hole_id = hole["hole_id"]
            traj = hole["trajectory"]
            assay_hole = assay_df[assay_df["ID"] == hole_id].copy()

            # Assays filtrados al sondaje
            assay_hole = assay_hole.sort_values("FROM")
            max_depth = traj[-1]["depth"]

            comp_start = 0.0

            while comp_start < max_depth:
                comp_end = comp_start + length

                # Intervalos que intersectan el composito
                intervals = assay_hole[
                    (assay_hole["FROM"] < comp_end) &
                    (assay_hole["TO"] > comp_start)
                ]

                if len(intervals) < min_samples:
                    comp_start = comp_end
                    continue

                total = 0.0
                weight = 0.0
                # Promedio ponderado por longitud
                for _, row in intervals.iterrows():
                    v = row[variable]
                    if pd.isna(v):
                        continue
                    
                    # Aplicar top-cut si procede
                    if top_cut is not None and v > top_cut:
                        v = top_cut

                    # Longitud real en el solape
                    overlap = min(row["TO"], comp_end) - max(row["FROM"], comp_start)
                    if overlap > 0:
                        total += v * overlap
                        weight += overlap

                if weight > 0:
                    value = total / weight
                else:
                    value = None

                mid_depth = (comp_start + comp_end) / 2
                # Punto de trayectoria más cercano al mid-depth
                xyz = min(traj, key=lambda p: abs(p["depth"] - mid_depth))

                composites.append({
                    "hole_id": hole_id,
                    "from": comp_start,
                    "to": comp_end,
                    "value": None if value is None else float(value),
                    "x": xyz["x"],
                    "y": xyz["y"],
                    "z": xyz["z"],
                    "mid_depth": mid_depth
                })

                comp_start = comp_end

        return composites

    # ---------------------------------------------------------
    # 4) RENDER ASSAY
    # ---------------------------------------------------------

    def build_render(self, assay_model, feature, assay_path=None):
        """
        Genera un render continuo para cada punto de la trayectoria,
        evaluando la variable numérica real del assay.

        Es decir:
            Para cada punto de trayectoria:
                depth → buscar intervalo FROM–TO que contiene depth.

        Esto permite colorear el sondaje en 3D según oro, cobre, densidad, etc.

        Args:
            assay_model: drillholes ya estructurados.
            feature (str): Columna numérica a representar.

        Returns:
            list: [{hole_id, points:[{x,y,z,depth,value}]}]
        """

        if assay_path is None:
            assay_path = "backend/uploads/assay.csv"
        
        df_assay = pd.read_csv(assay_path)

        render = []

        for hole in assay_model:

            hole_id = hole["hole_id"]
            trajectory = hole["trajectory"]

            assay_hole = df_assay[df_assay["ID"] == hole_id].copy()
            assay_hole["FROM"] = pd.to_numeric(assay_hole["FROM"], errors="coerce")
            assay_hole["TO"] = pd.to_numeric(assay_hole["TO"], errors="coerce")

            points = []

            for p in trajectory:
                depth = p["depth"]
                # Intersección con intervalos
                row = assay_hole[
                    (assay_hole["FROM"] <= depth) &
                    (assay_hole["TO"] >= depth)
                ]

                if row.empty:
                    value = None
                else:
                    raw = row.iloc[0][feature]
                    value = None if pd.isna(raw) else float(raw)

                points.append({
                    "x": p["x"],
                    "y": p["y"],
                    "z": p["z"],
                    "depth": depth,
                    "value": value
                })

            render.append({
                "hole_id": hole_id,
                "points": points
            })

        return render



    # ---------------------------------------------------------
    # 5) KRIGING (3D con variograma esférico)
    # ---------------------------------------------------------
    def krige_blocks(self, composites, blocks, variable):
        """
        Krigeo local básico usando variograma esférico:

            γ(h) = nugget + sill * (1.5h/R - 0.5(h/R)^3)   si h <= R
            γ(h) = nugget + sill                           si h > R

        - Selecciona vecinos mediante KDTree
        - Limita máximo 12 vecinos
        - Usa matriz de kriging ordinario
        - Incluye anti-singularidad (epsilon en diagonal)
        - Devuelve bloques con 'value' estimado

        Args:
            composites (list): Puntos 3D con valor.
            blocks (list): Bloques a estimar.
            variable (str): Nombre de variable estimada.

        Returns:
            list: Bloques con valor agregado.
        """
        #Extraer arrays numéricos
        xs = np.array([c["x"] for c in composites], float)
        ys = np.array([c["y"] for c in composites], float)
        zs = np.array([c["z"] for c in composites], float)
        vs = np.array([c["value"] for c in composites], float)

        #Filtrar NaN
        valid = ~np.isnan(vs)
        xs = xs[valid]
        ys = ys[valid]
        zs = zs[valid]
        vs = vs[valid]

        if len(xs) < 1:
            print("⚠ No hay composites válidos para kriging")
            for b in blocks:
                b["value"] = None
            return blocks

        # KDTree para búsqueda rápida
        tree = cKDTree(np.column_stack([xs, ys, zs]))

        # Parámetros de kriging local
        MAX_NEIGH = 12
        SEARCH_RADIUS = 100     # metros
        NUGGET = 0.05
        SILL = 1.0
        RANGE = 120

        def spherical(h):
            hr = h / RANGE
            return np.where(
                h <= RANGE,
                NUGGET + SILL * (1.5*hr - 0.5*(hr**3)),
                NUGGET + SILL
            )

        #Estimar bloque por bloque
        for b in blocks:

            bx, by, bz = b["x"], b["y"], b["z"]

            # Buscar vecinos cercanos
            idx = tree.query_ball_point([bx,by,bz], SEARCH_RADIUS)

            # Sin vecinos → no se puede krigear
            if len(idx) == 0:
                b["value"] = None
                continue

            # Si hay pocos vecinos → nearest neighbor
            if len(idx) < 3:
                b["value"] = float(vs[idx][0])
                continue

            # Limitar a máximo vecinos
            if len(idx) > MAX_NEIGH:
                dists = np.sqrt((xs[idx]-bx)**2 + (ys[idx]-by)**2 + (zs[idx]-bz)**2)
                nearest_idx = np.argsort(dists)[:MAX_NEIGH]
                idx = np.array(idx)[nearest_idx]

            # Matriz K (kriging)
            n = len(idx)
            P = np.zeros((n+1, n+1))
            for i in range(n):
                for j in range(n):
                    h = np.sqrt((xs[idx][i]-xs[idx][j])**2 +
                                (ys[idx][i]-ys[idx][j])**2 +
                                (zs[idx][i]-zs[idx][j])**2)
                    P[i,j] = spherical(h)

            # Condición Σλ = 1
            P[-1,:-1] = 1
            P[:-1,-1] = 1
            P[-1,-1] = 0

            # Vector k
            k = np.zeros(n+1)
            for i in range(n):
                h = np.sqrt((xs[idx][i]-bx)**2 +
                            (ys[idx][i]-by)**2 +
                            (zs[idx][i]-bz)**2)
                k[i] = spherical(h)
            k[-1] = 1

            # -------------------------------------------------
            # FIX ANTI-SINGULAR para evitar LinAlgError
            # -------------------------------------------------
            P += np.eye(P.shape[0]) * 1e-6

            try:
                w = np.linalg.solve(P, k)
            except np.linalg.LinAlgError:
                # fallback: nearest neighbor
                b["value"] = float(vs[idx][0])
                continue

            lam = w[:-1]

            # Estimación
            estimate = np.dot(lam, vs[idx])

            # -------------------------------------------------
            # FIX: JSON no acepta NaN
            # -------------------------------------------------
            if np.isnan(estimate):
                b["value"] = None
            else:
                b["value"] = float(estimate)

        return blocks


    # ---------------------------------------------------------
    # NUEVO: construir samples 3D usando cada intervalo (INTERPOLACIÓN REAL)
    # ---------------------------------------------------------
    def build_samples_from_assay(self, drillholes, assay_df, variable, top_cut=None):
        """
        Construye un set de "samples" 3D usando cada intervalo FROM–TO real del assay.

        Para cada intervalo:
            - Calcula mid_depth
            - Interpola trayectoria (XYZ) entre estaciones para ese depth
            - Asigna valor del assay
            - Devuelve un punto espacial real (sample)

        Este método es más geológicamente correcto que trabajar sólo con compositos.

        Returns:
            list[dict]: samples 3D
        """
        samples = []

        for hole in drillholes:
            hole_id = hole["hole_id"]
            traj = hole["trajectory"]

            # Ordenar trayectoria por profundidad
            traj_sorted = sorted(traj, key=lambda p: p["depth"])

            # Ensayos del sondaje
            ah = assay_df[assay_df["ID"] == hole_id].copy()
            ah["FROM"] = pd.to_numeric(ah["FROM"], errors="coerce")
            ah["TO"]   = pd.to_numeric(ah["TO"], errors="coerce")
            ah = ah.sort_values("FROM")

            # Recorrer cada intervalo
            for _, row in ah.iterrows():

                v = row[variable]
                if pd.isna(v):
                    continue

                if top_cut is not None and v > top_cut:
                    v = top_cut

                from_d = float(row["FROM"])
                to_d   = float(row["TO"])
                mid    = 0.5 * (from_d + to_d)

                # -----------------------------------------
                # Interpolación lineal en la trayectoria
                # -----------------------------------------
                found = False
                for i in range(len(traj_sorted) - 1):
                    d1 = traj_sorted[i]["depth"]
                    d2 = traj_sorted[i+1]["depth"]

                    if d1 <= mid <= d2:
                        t = (mid - d1) / (d2 - d1)

                        x = traj_sorted[i]["x"] + t * (traj_sorted[i+1]["x"] - traj_sorted[i]["x"])
                        y = traj_sorted[i]["y"] + t * (traj_sorted[i+1]["y"] - traj_sorted[i]["y"])
                        z = traj_sorted[i]["z"] + t * (traj_sorted[i+1]["z"] - traj_sorted[i]["z"])

                        found = True
                        break

                if not found:
                    # Fallback: punto más cercano
                    p = min(traj_sorted, key=lambda p: abs(p["depth"] - mid))
                    x, y, z = p["x"], p["y"], p["z"]

                samples.append({
                    "hole_id": hole_id,
                    "from": from_d,
                    "to": to_d,
                    "mid_depth": mid,
                    "value": float(v),
                    "x": float(x),
                    "y": float(y),
                    "z": float(z),
                })

        return samples


        # ---------------------------------------------------------
    # 6) LEER SUPERFICIE DESDE DXF (TOPOGRAFÍA / POLILÍNEA / MALLA)
    # ---------------------------------------------------------
    def load_dxf_surface(self, dxf_path):

        """
        Lee geometrías 3D desde un archivo DXF.

        Soporta:
            - LWPOLYLINE (curvas con elevación fija)
            - POLYLINE 3D
            - 3DFACE (superficies reales)
            - MESH (mallas 3D)
            - LINE (líneas 2D o 3D)
            - SPLINE (curvas suaves)
        """

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        points = []

        # ---------------------------------------------------------
        # 1) LWPOLYLINE (muy común en curvas de nivel)
        # ---------------------------------------------------------
        for e in msp.query("LWPOLYLINE"):
            elev = float(e.dxf.elevation) if e.dxf.elevation is not None else 0.0
            for x, y, _, _ in e.get_points():
                points.append({"x": float(x), "y": float(y), "z": elev})

        # ---------------------------------------------------------
        # 2) POLYLINE 3D
        # ---------------------------------------------------------
        for e in msp.query("POLYLINE"):
            if e.is_3d_polyline:
                for v in e.vertices:
                    points.append({
                        "x": float(v.dxf.location.x),
                        "y": float(v.dxf.location.y),
                        "z": float(v.dxf.location.z)
                    })

        # ---------------------------------------------------------
        # 3) 3DFACE (superficie real)
        # ---------------------------------------------------------
        for e in msp.query("3DFACE"):
            for attr in ["vtx0", "vtx1", "vtx2", "vtx3"]:
                try:
                    v = getattr(e.dxf, attr)
                    if v is None:
                        continue
                    x, y, z = v
                    points.append({"x": float(x), "y": float(y), "z": float(z)})
                except:
                    pass

        # ---------------------------------------------------------
        # 4) MESH
        # ---------------------------------------------------------
        for e in msp.query("MESH"):
            for v in e.vertices:
                points.append({
                    "x": float(v.dxf.location.x),
                    "y": float(v.dxf.location.y),
                    "z": float(v.dxf.location.z)
                })

        # ---------------------------------------------------------
        # 5) LINE (curvas sin elevación o con elevación por vértice)
        # ---------------------------------------------------------
        for e in msp.query("LINE"):
            start = e.dxf.start
            end = e.dxf.end

            points.append({
                "x": float(start.x),
                "y": float(start.y),
                "z": float(start.z)
            })
            points.append({
                "x": float(end.x),
                "y": float(end.y),
                "z": float(end.z)
            })

        # ---------------------------------------------------------
        # 6) SPLINE (usa puntos de control)
        # ---------------------------------------------------------
        for e in msp.query("SPLINE"):
            for v in e.control_points:
                # Los splines pueden estar en 2D (sin Z)
                x, y = float(v[0]), float(v[1])
                z = float(v[2]) if len(v) > 2 else 0.0
                points.append({"x": x, "y": y, "z": z})

        return points


    # ---------------------------------------------------------
    # 7) AUTO BOUNDING BOX (Drillholes + Superficie DXF)
    # ---------------------------------------------------------
    def auto_detect_bbox(self, drillholes, surface_points=None, padding=20):
        """
        Detecta automáticamente el bounding box del proyecto usando:
        - Trayectorias de sondajes (coordenadas reales X,Y,Z)
        - Superficie DXF (si existe)
        - Padding para expandir el cubo
        
        Return:
            minX, maxX, minY, maxY, minZ, maxZ
        """

        xs, ys, zs = [], [], []

        # 1) Drillholes: tomar TODAS las coordenadas 3D
        for dh in drillholes:
            for p in dh["trajectory"]:
                xs.append(p["x"])
                ys.append(p["y"])
                zs.append(p["z"])

        # 2) Superficie DXF
        if surface_points:
            for p in surface_points:
                xs.append(p["x"])
                ys.append(p["y"])
                zs.append(p["z"])

        if len(xs) == 0:
            raise ValueError("No existen puntos para calcular bounding box.")

        # BBOX real
        minX, maxX = min(xs), max(xs)
        minY, maxY = min(ys), max(ys)
        minZ, maxZ = min(zs), max(zs)

        # Padding para margen espacial del block model
        minX -= padding
        minY -= padding
        minZ -= padding

        maxX += padding
        maxY += padding
        maxZ += padding

        return minX, maxX, minY, maxY, minZ, maxZ
