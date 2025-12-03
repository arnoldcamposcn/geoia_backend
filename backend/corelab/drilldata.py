import pandas as pd

class DrillData:

    """
    DrillData gestiona los archivos de collar, survey y litología.
    Convierte automáticamente las columnas originales al formato estándar
    utilizado internamente por el motor 3D (ID, X, Y, Z, AT, AZ, DIP, etc).

    Esta clase:
    - Lee los CSV originales
    - Aplica el mapeo de columnas seleccionado por el usuario
    - Valida que los campos mínimos existan
    - Proporciona helpers para recuperar los datos de cada sondaje
    """

    def __init__(
        self,
        collar_file: str,
        survey_file: str,
        lith_file: str,
        collar_map: dict,
        survey_map: dict,
        lith_map: dict
    ):

        """
        agreggar filtro ----- campo que estimados.....

    Inicializa DrillData cargando los archivos y aplicando las reglas
    de mapeo personalizadas.

    Args:
        collar_file (str): Ruta del archivo COLLAR estandarizado.
        survey_file (str): Ruta del archivo SURVEY estandarizado.
        lith_file   (str): Ruta del archivo LITH estandarizado.
        collar_map (dict): Mapeo interno → archivo. Ej: {"ID":"BHID"}.
        survey_map (dict): Mapeo interno → archivo.
        lith_map   (dict): Mapeo interno → archivo.

    Notas:
        El backend usa nombres internos fijos:
        COLLAR → ID, X, Y, Z  
        SURVEY → ID, AT, AZ, DIP  
        LITH   → ID, FROM, TO, ROCK
    """

        # Cargar dataframes originales
        self.collar_raw = pd.read_csv(collar_file)
        self.survey_raw = pd.read_csv(survey_file)
        self.lith_raw   = pd.read_csv(lith_file)

        # IMPORTANTE:
        # invertir mapping → el CSV tiene "BHID" pero CoreLab espera "ID"
        # mapping detectado = { "ID":"BHID" }
        # invertido = { "BHID":"ID" }
        self.collar_map = { v: k for k, v in collar_map.items() }
        self.survey_map = { v: k for k, v in survey_map.items() }
        self.lith_map   = { v: k for k, v in lith_map.items() }

        # Aplicar renombrado real
        self.collar = self.collar_raw.rename(columns=self.collar_map)
        self.survey = self.survey_raw.rename(columns=self.survey_map)
        self.lith   = self.lith_raw.rename(columns=self.lith_map)

        # Validaciones

        # Requeridos en COLLAR
        for col in ["ID", "X", "Y", "Z"]:
            if col not in self.collar.columns:
                raise ValueError(f"ERROR: falta columna {col} en COLLAR después del mapeo")


    # Requeridos en SURVEY
        for col in ["ID", "AT", "AZ", "DIP"]:
            if col not in self.survey.columns:
                raise ValueError(f"ERROR: falta columna {col} en SURVEY después del mapeo")

    # Requeridos en LITH
        for col in ["ID", "FROM", "TO"]:
            if col not in self.lith.columns:
                raise ValueError(f"ERROR: falta columna {col} en LITH después del mapeo")

    # Helpers
    def get_collar(self, hole_id):

        """
        Devuelve la fila COLLAR correspondiente a un sondaje.

        Args:
            hole_id (str): Identificador del sondaje.

        Returns:
            pandas.Series | None
        """
        df = self.collar[self.collar["ID"] == hole_id]
        return df.iloc[0] if not df.empty else None

    def get_survey(self, hole_id):
        """
        Devuelve el dataframe con las mediciones SURVEY del sondaje.

        Args:
            hole_id (str)

        Returns:
            pandas.DataFrame
        """
        return self.survey[self.survey["ID"] == hole_id]

    def get_lith(self, hole_id):
        """
        Devuelve el dataframe con las litologías del sondaje.

        Args:
            hole_id (str)

        Returns:
            pandas.DataFrame
        """
        return self.lith[self.lith["ID"] == hole_id]
