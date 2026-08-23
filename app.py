import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import gc
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pdf2image import convert_from_path
import tempfile
import shutil
import pandas as pd
import io
import time
import json
import difflib
import google.generativeai as genai

# --- PROTECCIÓN PARA IMÁGENES MASIVAS ---
Image.MAX_IMAGE_PIXELS = None 

# --- INICIALIZAR MEMORIA DE STREAMLIT ---
if "procesado" not in st.session_state:
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.excel_bytes = None
    st.session_state.df_resultados = pd.DataFrame()
    st.session_state.alertas = 0
    st.session_state.planillas_reales = 0

def reiniciar_app():
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.excel_bytes = None
    st.session_state.df_resultados = pd.DataFrame()
    st.session_state.alertas = 0
    st.session_state.planillas_reales = 0

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="sergemLogo.ico")

# --- CONFIGURACIÓN DE GEMINI ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception:
    st.error("⚠️ Falta configurar la GEMINI_API_KEY en los Secrets de Streamlit.")

# --- ENCABEZADO CON LOGO ---
col1, col2 = st.columns([1, 4])
with col1:
    if os.path.exists("sergemLogo.png"):
        st.image("sergemLogo.png", width=180)
    else:
        st.write("🏢 SERGEM")
with col2:
    st.title("Automatización de Planillas SERGEM")
    st.markdown("Procesamiento inteligente con deducción por colaborador (Mensajero), cruce de ciudades y orientación perfecta.")

# --- CARGA DE DATOS ---
@st.cache_data
def cargar_base_datos(ruta_archivo="centrosDeCostos.xlsx"):
    try:
        df = pd.read_excel(ruta_archivo)
        df.columns = df.columns.str.upper().str.strip()
        
        # Mapeo dinámico para acoplarse al Excel
        col_cenco = 'CENTRO_COSTO' if 'CENTRO_COSTO' in df.columns else next((col for col in df.columns if 'CENCO' in col), None)
        col_empresa = 'EMPRESA' if 'EMPRESA' in df.columns else next((col for col in df.columns if 'CLIENTE' in col), None)
        col_ciudad = 'CIUDAD' if 'CIUDAD' in df.columns else next((col for col in df.columns if 'SUCURSAL' in col), None)
        col_nombre = 'MENSAJERO' if 'MENSAJERO' in df.columns else next((col for col in df.columns if 'NOMBRE' in col or 'COLAB' in col), None)

        # Si alguna columna no existe en el excel, la creamos vacía para evitar errores
        for col, default_name in zip([col_cenco, col_empresa, col_ciudad, col_nombre], ['CENCO_BD', 'CLIENTE_BD', 'CIUDAD_BD', 'NOMBRE_BD']):
            if col not in df.columns:
                df[default_name] = "No detectado"
            else:
                df = df.rename(columns={col: default_name})
        
        # Limpieza profunda de los textos del excel (Quita tabulaciones y espacios extra)
        for col in ['CENCO_BD', 'CLIENTE_BD', 'CIUDAD_BD', 'NOMBRE_BD']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('\t', '').str.strip().str.upper()
            
        return df
    except Exception as e:
        return pd.DataFrame(columns=['CENCO_BD', 'CLIENTE_BD', 'CIUDAD_BD', 'NOMBRE_BD'])

# --- FUNCIONES DE IA Y CRUCE INTELIGENTE ---
def obtener_mejor_similitud_nombre(ocr_name, name_list):
    """Algoritmo tolerante a errores ortográficos o nombres incompletos"""
    if not ocr_name or ocr_name == "NO DETECTADO": return None
    
    matches = difflib.get_close_matches(ocr_name, name_list, n=1, cutoff=0.6)
    if matches:
        return matches[0]
        
    ocr_parts = set(ocr_name.split())
    best_match = None
    best_score = 0
    for name in name_list:
        name_parts = set(name.split())
        if not ocr_parts or not name_parts: continue
        score = len(ocr_parts.intersection(name_parts)) / float(len(ocr_parts))
        if score > best_score and score >= 0.5: 
            best_score = score
            best_match = name
    return best_match

def analizar_con_gemini(imagen_pil):
    img_api = imagen_pil.copy()
    img_api.thumbnail((1024, 1024), Image.Resampling.LANCZOS) 
    
    prompt = """
    Eres un asistente experto en lectura de documentos. Analiza esta planilla de 'Registro de Prestación de Servicios'.
    Busca cuidadosamente los campos solicitados y devuelve estrictamente un objeto JSON con estas claves:
    {
        "NOMBRE": "Nombre completo del colaborador, mensajero o conductor. Suele estar junto a 'NOMBRE:'. Si no hay, pon 'No detectado'",
        "CLIENTE": "Nombre de la empresa cliente (Ej: Covacrans, Exito, Olimpica, Surtimax). Busca el logo o la palabra junto a 'CLIENTE:'. Si no hay, pon 'No detectado'",
        "SUCURSAL": "Ciudad o nombre de la sucursal. Si no hay, pon 'No detectado'",
        "CENCO": "Número del centro de costo. Revisa bien la esquina superior derecha junto a 'CENCO:' o 'Código' (ej: 416, 30302, P-1234). Si está vacío o hay firmas que tapan el número, pon 'No detectado'",
        "ESTA_DE_CABEZA": true o false (responde true SOLO si la planilla está girada y el texto principal se lee al revés/de cabeza)
    }
    """
    
    for intento in range(4): 
        try:
            time.sleep(3.5) # Freno de seguridad para procesar quincenas sin bloquear la API
            response = model.generate_content([prompt, img_api])
            texto_respuesta = response.text
            
            if "```json" in texto_respuesta:
                texto_respuesta = texto_respuesta.split("```json")[1].split("```")[0]
            elif "```" in texto_respuesta:
                texto_respuesta = texto_respuesta.split("```")[1].split("```")[0]
                
            return json.loads(texto_respuesta.strip())
        except Exception:
            time.sleep(5)
            
    return {"NOMBRE": "No detectado", "CLIENTE": "No detectado", "SUCURSAL": "No detectado", "CENCO": "No detectado", "ESTA_DE_CABEZA": False}

def cruzar_datos_inteligente(cenco_ocr, cliente_ocr, sucursal_ocr, nombre_ocr, df_base):
    empresa_asignada = "No asignada (Validar)"
    
    cenco_cl = str(cenco_ocr).upper().strip()
    cliente_cl = str(cliente_ocr).upper().strip()
    sucursal_cl = str(sucursal_ocr).upper().strip()
    nombre_cl = str(nombre_ocr).upper().strip()
    
    if df_base.empty:
        return cenco_cl if cenco_cl not in ["NO DETECTADO", "NULL", "NONE", "VACÍO", ""] else "No detectado", "BD no cargada"

    # 1. Búsqueda por CENCO explícito en la hoja
    if cenco_cl and cenco_cl not in ["NO DETECTADO", "NULL", "NONE", "VACÍO", ""]:
        coincidencia = df_base[df_base['CENCO_BD'] == cenco_cl]
        if not coincidencia.empty:
            return cenco_cl, coincidencia.iloc[0]['CLIENTE_BD']
            
        cenco_cl_clean = cenco_cl.replace("-", "").replace(" ", "")
        coincidencia2 = df_base[df_base['CENCO_BD'].str.replace("-", "").str.replace(" ", "") == cenco_cl_clean]
        if not coincidencia2.empty:
            return coincidencia2.iloc[0]['CENCO_BD'], coincidencia2.iloc[0]['CLIENTE_BD']

    cenco_final = "No detectado"

    # 2. Búsqueda Detective: Deducción usando el Nombre del Colaborador (MENSAJERO)
    if nombre_cl != "NO DETECTADO":
        nombres_bd = df_base['NOMBRE_BD'].unique().tolist()
        match_nombre = obtener_mejor_similitud_nombre(nombre_cl, nombres_bd)
        
        if match_nombre:
            subset_emp = df_base[df_base['NOMBRE_BD'] == match_nombre]
            
            # Si el mensajero solo tiene un CENCO en toda la base, es ese
            if len(subset_emp['CENCO_BD'].unique()) == 1:
                return str(subset_emp.iloc[0]['CENCO_BD']), f"{subset_emp.iloc[0]['CLIENTE_BD']} (Por Colaborador)"
            
            # Si tiene varios clientes, filtramos por la ciudad
            if sucursal_cl != "NO DETECTADO":
                ciudades_emp = subset_emp['CIUDAD_BD'].unique().tolist()
                match_ciudad = difflib.get_close_matches(sucursal_cl, ciudades_emp, n=1, cutoff=0.5)
                if match_ciudad:
                    fila = subset_emp[subset_emp['CIUDAD_BD'] == match_ciudad[0]].iloc[0]
                    return str(fila['CENCO_BD']), f"{fila['CLIENTE_BD']} (Por Colab + Ciudad)"

    # 3. Cruce por Cliente + Ciudad (Fallback)
    if cliente_cl != "NO DETECTADO":
        clientes_bd = df_base['CLIENTE_BD'].unique().tolist()
        match_cliente = difflib.get_close_matches(cliente_cl, clientes_bd, n=1, cutoff=0.40)
        
        if match_cliente:
            emp_match = match_cliente[0]
            subset_cliente = df_base[df_base['CLIENTE_BD'] == emp_match]
            
            if sucursal_cl != "NO DETECTADO":
                ciudades_cliente = subset_cliente['CIUDAD_BD'].unique().tolist()
                match_ciudad = difflib.get_close_matches(sucursal_cl, ciudades_cliente, n=1, cutoff=0.5)
                if match_ciudad:
                    fila = subset_cliente[subset_cliente['CIUDAD_BD'] == match_ciudad[0]].iloc[0]
                    cenco_res = str(fila['CENCO_BD'])
                    return cenco_res if cenco_res != "NAN" else "No detectado", f"{emp_match} (Cruce Exacto)"
                    
            cenco_res = str(subset_cliente.iloc[0]['CENCO_BD'])
            return cenco_res if cenco_res != "NAN" else "No detectado", f"{emp_match} (Sin ciudad exacta)"
            
    return "No detectado", empresa_asignada

def optimizar_y_leer(imagen_pil, df_base):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # 1. FORZAR HORIZONTAL SIEMPRE antes de la IA
    ancho, alto = imagen_pil.size
    if alto > ancho:
        imagen_pil = imagen_pil.rotate(90, expand=True)
        
    # 2. Consultar a la IA
    datos_ia = analizar_con_gemini(imagen_pil)
    
    # 3. Si la hoja horizontal quedó con el texto al revés (de cabeza), rotar 180 grados
    if datos_ia.get("ESTA_DE_CABEZA", False):
        imagen_pil = imagen_pil.rotate(180, expand=True)

    # 4. Recorte inferior de marcas de escáner
    ancho, alto = imagen_pil.size
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
    
    cenco_base = datos_ia.get("CENCO", "No detectado")
    cliente_base = datos_ia.get("CLIENTE", "No detectado")
    nombre_base = datos_ia.get("NOMBRE", "No detectado")
    sucursal_base = datos_ia.get("SUCURSAL", "No detectado")
    
    cenco_final, empresa_final = cruzar_datos_inteligente(cenco_base, cliente_base, sucursal_base, nombre_base, df_base)
    
    # --- ESTAMPADO ESTRICTO ---
    dibujo = ImageDraw.Draw(imagen_pil)
    try:
        tamano_fuente = int(imagen_pil.size[1] * 0.03) 
        fuente = ImageFont.truetype("arial.ttf", tamano_fuente)
    except IOError:
        fuente = ImageFont.load_default()
        
    texto_sello = f" CENCO: {cenco_final} " if cenco_final != "No detectado" else " CENCO: No detectado "
    
    try:
        caja_texto = dibujo.textbbox((0, 0), texto_sello, font=fuente)
        ancho_texto = caja_texto[2] - caja_texto[0]
        alto_texto = caja_texto[3] - caja_texto[1]
    except AttributeError:
        ancho_texto, alto_texto = dibujo.textsize(texto_sello, font=fuente)

    x = imagen_pil.size[0] - ancho_texto - 20
    y = 20
    dibujo.rectangle((x, y, x + ancho_texto, y + alto_texto + 10), fill="white", outline="black", width=2)
    dibujo.text((x, y + 5), texto_sello, fill="red", font=fuente)
    
    return imagen_pil, nombre_base, cenco_final, cliente_base, sucursal_base, empresa_final

# --- EXTRACCIÓN MASIVA ---
def extraer_adjuntos(contenido_bytes, directorio_salida, es_zip=False):
    adjuntos = []
    extensiones_validas = ('.pdf', '.jpeg', '.jpg', '.png', '.bmp', '.webp', '.tiff')
    
    if es_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(contenido_bytes), 'r') as zip_ref:
                for zip_filename in zip_ref.namelist():
                    if zip_filename.lower().endswith(extensiones_validas):
                        file_content = zip_ref.read(zip_filename)
                        base_name = os.path.basename(zip_filename)
                        if not base_name: continue
                        
                        ruta_archivo = os.path.join(directorio_salida, base_name)
                        contador = 1
                        while os.path.exists(ruta_archivo):
                            nombre_base, ext = os.path.splitext(base_name)
                            ruta_archivo = os.path.join(directorio_salida, f"{nombre_base}_{contador}{ext}")
                            contador += 1
                        with open(ruta_archivo, 'wb') as f_out:
                            f_out.write(file_content)
                        adjuntos.append(ruta_archivo)
        except Exception:
            pass
    else:
        msg = email.message_from_bytes(contenido_bytes, policy=policy.default)
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                continue
            filename = part.get_filename()
            if filename:
                ext_lower = filename.lower()
                if ext_lower.endswith(extensiones_validas):
                    ruta_archivo = os.path.join(directorio_salida, filename)
                    contador = 1
                    while os.path.exists(ruta_archivo):
                        nombre_base, ext = os.path.splitext(filename)
                        ruta_archivo = os.path.join(directorio_salida, f"{nombre_base}_{contador}{ext}")
                        contador += 1
                    with open(ruta_archivo, 'wb') as new_file:
                        new_file.write(part.get_payload(decode=True))
                    adjuntos.append(ruta_archivo)
                elif ext_lower.endswith('.zip'):
                    adjuntos.extend(extraer_adjuntos(part.get_payload(decode=True), directorio_salida, es_zip=True))
    return adjuntos

def procesar_archivo_comprimido(archivo_subido, directorio_salida):
    todos_los_adjuntos = []
    if archivo_subido.name.endswith(('.tgz', '.tar.gz')):
        with tarfile.open(fileobj=archivo_subido, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".eml"):
                    f = tar.extractfile(member)
                    if f is not None:
                        todos_los_adjuntos.extend(extraer_adjuntos(f.read(), directorio_salida))
    elif archivo_subido.name.endswith('.zip'):
        with zipfile.ZipFile(archivo_subido, 'r') as zip_ref:
            for filename in zip_ref.namelist():
                if filename.endswith(".eml"):
                    with zip_ref.open(filename) as f:
                        todos_los_adjuntos.extend(extraer_adjuntos(f.read(), directorio_salida))
    return todos_los_adjuntos

def procesar_documento(ruta_archivo, df_base, temp_dir):
    rutas_optimizadas = []
    resultados = []
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_archivo, dpi=130)
            for i, pagina in enumerate(paginas):
                pagina_rgb = pagina.convert('RGB')
                if pagina_rgb.size[0] < 500 or pagina_rgb.size[1] < 500: continue
                
                img_final, nombre, cenco, cliente, sucursal, empresa = optimizar_y_leer(pagina_rgb, df_base)
                ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
                img_final.save(ruta_temp, 'JPEG', quality=85)
                rutas_optimizadas.append(ruta_temp)
                resultados.append((nombre, cenco, cliente, sucursal, empresa))
                
                del pagina_rgb, img_final
                gc.collect()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            if img.size[0] < 500 or img.size[1] < 500: return [], []
            
            img_final, nombre, cenco, cliente, sucursal, empresa = optimizar_y_leer(img, df_base)
            ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
            img_final.save(ruta_temp, 'JPEG', quality=85)
            rutas_optimizadas.append(ruta_temp)
            resultados.append((nombre, cenco, cliente, sucursal, empresa))
            
            del img, img_final
            gc.collect()
            
        return rutas_optimizadas, resultados
    except Exception:
        return [], []

def generador_imagenes(rutas):
    for ruta in rutas:
        with Image.open(ruta) as img:
            yield img.convert('RGB')
        gc.collect()

# --- FLUJO PRINCIPAL DE INTERFAZ ---

if not st.session_state.procesado:
    archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte masivo de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

    if archivo_zimbra is not None:
        if st.button("🚀 Procesar Quincena y Generar Reportes", type="primary"):
            df_base = cargar_base_datos()
            temp_dir = tempfile.mkdtemp()
            
            with st.spinner("📦 Extrayendo archivos, enderezando con IA y cruzando datos..."):
                adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
                
            if not adjuntos:
                st.warning("No se encontraron planillas válidas.")
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                resultados_tabla = []
                todas_las_rutas_impresion = []
                alertas = 0
                planillas_reales = 0 
                
                for i, archivo in enumerate(adjuntos):
                    nombre_archivo = os.path.basename(archivo)
                    status_text.text(f"Evaluando como detective: {nombre_archivo}")
                    
                    rutas_est, datos_extraidos = procesar_documento(archivo, df_base, temp_dir)
                    
                    if not rutas_est:
                        progress_bar.progress((i + 1) / len(adjuntos))
                        continue
                    
                    for dato in datos_extraidos:
                        planillas_reales += 1
                        todas_las_rutas_impresion.append(rutas_est[datos_extraidos.index(dato)])
                        
                        nombre, cenco, cliente, sucursal, empresa = dato
                        if cenco == "No detectado":
                            alertas += 1
                            
                        resultados_tabla.append({
                            "Documento": nombre_archivo,
                            "Mensajero/Colab": nombre,
                            "CENCO Final": cenco,
                            "Cliente en Planilla": cliente,
                            "Ciudad/Sucursal": sucursal,
                            "Cliente Oficial (BD)": empresa
                        })
                    
                    progress_bar.progress((i + 1) / len(adjuntos))
                    gc.collect()
                
                # --- GUARDAR RESULTADOS EN MEMORIA (STATE) ---
                st.session_state.df_resultados = pd.DataFrame(resultados_tabla)
                st.session_state.alertas = alertas
                st.session_state.planillas_reales = planillas_reales
                
                # Excel a bytes
                excel_buffer = io.BytesIO()
                st.session_state.df_resultados.to_excel(excel_buffer, index=False)
                st.session_state.excel_bytes = excel_buffer.getvalue()
                
                # PDF a bytes
                if todas_las_rutas_impresion:
                    pdf_buffer = io.BytesIO()
                    with Image.open(todas_las_rutas_impresion[0]) as primera_img:
                        primera_img_rgb = primera_img.convert('RGB')
                        if len(todas_las_rutas_impresion) > 1:
                            primera_img_rgb.save(pdf_buffer, format="PDF", save_all=True, append_images=generador_imagenes(todas_las_rutas_impresion[1:]))
                        else:
                            primera_img_rgb.save(pdf_buffer, format="PDF")
                    st.session_state.pdf_bytes = pdf_buffer.getvalue()
                
                st.session_state.procesado = True
                shutil.rmtree(temp_dir, ignore_errors=True)
                st.rerun()

# --- VISTA DE RESULTADOS (Memoria permanente) ---
if st.session_state.procesado:
    st.success(f"✅ ¡Procesamiento masivo finalizado! Se orientaron y cruzaron {st.session_state.planillas_reales} planillas con éxito.")
    
    if st.session_state.alertas > 0:
        st.warning(f"⚠️ {st.session_state.alertas} planilla(s) quedaron con 'CENCO: No detectado'. Verifica el Excel para ajustes manuales.")
    
    st.dataframe(st.session_state.df_resultados, use_container_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="📊 Descargar Reporte Completo (Excel)",
            data=st.session_state.excel_bytes,
            file_name="Reporte_Quincenal_SERGEM.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )
    with col2:
        if st.session_state.pdf_bytes:
            st.download_button(
                label="🖨️ Descargar Todas las Planillas",
                data=st.session_state.pdf_bytes,
                file_name="Planillas_SERGEM_Alineadas.pdf",
                mime="application/pdf",
                type="primary"
            )
            
    st.write("---")
    if st.button("♻️ Finalizar y Procesar Nueva Quincena"):
        reiniciar_app()
        st.rerun()
