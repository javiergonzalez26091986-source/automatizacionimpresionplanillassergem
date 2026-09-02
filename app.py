import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import easyocr
import gc
import numpy as np
from PIL import Image, ImageOps
import fitz  # PyMuPDF
import tempfile
import shutil
import io
import pandas as pd
import hashlib
import json

# --- PROTECCIÓN PARA IMÁGENES MASIVAS Y MEMORIA ---
Image.MAX_IMAGE_PIXELS = None 

# --- MEMORIA HISTÓRICA (FILTRO DE DUPLICADOS) ---
ARCHIVO_HISTORIAL = "registro_sergem.json"

def cargar_historial():
    if os.path.exists(ARCHIVO_HISTORIAL):
        try:
            with open(ARCHIVO_HISTORIAL, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def guardar_historial(historial_set):
    with open(ARCHIVO_HISTORIAL, 'w') as f:
        json.dump(list(historial_set), f)

def obtener_hash(ruta_archivo):
    hasher = hashlib.md5()
    with open(ruta_archivo, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

# --- INICIALIZAR MEMORIA DE STREAMLIT ---
if "procesado" not in st.session_state:
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.planillas_reales = 0
    st.session_state.planillas_omitidas = 0
    st.session_state.df_resultados = pd.DataFrame()

def reiniciar_app():
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.planillas_reales = 0
    st.session_state.planillas_omitidas = 0
    st.session_state.df_resultados = pd.DataFrame()
    st.rerun()

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="sergemLogo.ico")

# --- ESTILOS CSS A PRUEBA DE MODO OSCURO Y SIN BOTONES DE STREAMLIT ---
st.markdown("""
    <style>
    /* Ocultar botones de GitHub, Share, menú superior y pie de página de Streamlit */
    header {visibility: hidden !important;}
    [data-testid="stToolbar"] {visibility: hidden !important;}
    #MainMenu {visibility: hidden !important;}
    footer {visibility: hidden !important;}

    /* Forzar fondo claro en la app principal y en la barra lateral */
    .stApp, [data-testid="stSidebar"] { background-color: #f4f6f9 !important; }
    
    /* Forzar texto oscuro para títulos, etiquetas y textos en la barra lateral */
    h1, h2, h3, p, span, label, .stMarkdown, [data-testid="stSidebar"] p { color: #1e1e1e !important; }
    [data-testid="stHeader"] { background-color: rgba(0,0,0,0) !important; }
    
    /* Iluminar la caja donde se arrastran los archivos */
    [data-testid="stFileUploadDropzone"] {
        background-color: #ffffff !important;
        border: 2px dashed #e63946 !important;
    }
    [data-testid="stFileUploadDropzone"] * { 
        color: #1e1e1e !important; 
        fill: #e63946 !important; /* Colorea el ícono de la nube */
    }
    
    /* Estilo agresivo para garantizar botones rojos brillantes con texto blanco */
    div[data-testid="stButton"] > button {
        background-color: #e63946 !important;
        color: #ffffff !important;
        border-radius: 8px !important;
        border: 1px solid #d62828 !important;
        font-weight: bold !important;
        opacity: 1 !important;
    }
    div[data-testid="stButton"] > button:hover { 
        background-color: #d62828 !important; 
        border: 1px solid #1e1e1e !important; 
    }
    div[data-testid="stButton"] > button * { color: #ffffff !important; }
    
    /* Fondo claro para la tabla de resultados */
    [data-testid="stDataFrame"] { background-color: #ffffff !important; }
    </style>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 4])
with col1:
    if os.path.exists("sergemLogo.png"):
        st.image("sergemLogo.png", width=180)
    else:
        st.write("🏢 SERGEM")
with col2:
    st.title("Automatización de Planillas SERGEM")
    st.markdown("Orientación automática horizontal 100%.")

# --- CARGA DEL MODELO OCR ---
@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'], gpu=False)

# --- FUNCIONES DE LECTURA Y ORIENTACIÓN (LÓGICA ORIGINAL INTACTA) ---
def orientar_y_estandarizar(imagen_pil, reader):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # 1. BRÚJULA ORIGINAL INTACTA
    img_brujula = imagen_pil.copy()
    img_brujula.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    img_brujula = img_brujula.convert('L')
    
    mejor_angulo = 0
    max_score = -1
    
    for angulo in [0, 90, 180, 270]:
        img_rotada = img_brujula.rotate(angulo, expand=True)
        res = reader.readtext(np.array(img_rotada))
        
        score = sum(len(texto) for bbox, texto, prob in res if prob > 0.45)
        
        if score > max_score:
            max_score = score
            mejor_angulo = angulo
            
    del img_brujula
    gc.collect()

    # Rotar la imagen original
    imagen_pil = imagen_pil.rotate(mejor_angulo, expand=True)

    # 2. TRUCO DEL LIENZO INTACTO (Forzar Horizontal)
    ancho, alto = imagen_pil.size
    if alto > ancho:
        nuevo_ancho = int(alto * 1.3)
        canvas = Image.new('RGB', (nuevo_ancho, alto), 'white')
        offset_x = (nuevo_ancho - ancho) // 2
        canvas.paste(imagen_pil, (offset_x, 0))
        imagen_pil = canvas

    # 3. ESTANDARIZACIÓN PARA IMPRESIÓN PDF PERFECTA (TAMAÑO CARTA APAISADO)
    ancho_objetivo = 2200
    alto_objetivo = 1700
    
    ratio = min(ancho_objetivo / imagen_pil.width, alto_objetivo / imagen_pil.height)
    nuevo_ancho = int(imagen_pil.width * ratio)
    nuevo_alto = int(imagen_pil.height * ratio)
    
    img_redimensionada = imagen_pil.resize((nuevo_ancho, nuevo_alto), Image.Resampling.LANCZOS)
    
    lienzo_final = Image.new('RGB', (ancho_objetivo, alto_objetivo), 'white')
    
    pos_x = (ancho_objetivo - nuevo_ancho) // 2
    pos_y = (alto_objetivo - nuevo_alto) // 2
    lienzo_final.paste(img_redimensionada, (pos_x, pos_y))
    
    return lienzo_final

# --- EXTRACCIÓN MASIVA ---
def extraer_adjuntos(contenido_bytes, directorio_salida, es_zip=False):
    adjuntos = []
    extensiones_validas = ('.pdf', '.jpeg', '.jpg', '.png')
    
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

def procesar_documento(ruta_archivo, reader, temp_dir):
    rutas_optimizadas = []
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            doc = fitz.open(ruta_archivo)
            for i in range(len(doc)):
                pagina = doc.load_page(i)
                pix = pagina.get_pixmap(dpi=130)
                if pix.alpha:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                imagen_rgb = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                if imagen_rgb.size[0] < 500 or imagen_rgb.size[1] < 500: continue
                
                img_final = orientar_y_estandarizar(imagen_rgb, reader)
                ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
                img_final.save(ruta_temp, 'JPEG', quality=85)
                rutas_optimizadas.append(ruta_temp)
                
                del imagen_rgb, img_final, pix
                gc.collect()
            doc.close()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            if img.size[0] < 500 or img.size[1] < 500: return []
            
            img_final = orientar_y_estandarizar(img, reader)
            ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
            img_final.save(ruta_temp, 'JPEG', quality=85)
            rutas_optimizadas.append(ruta_temp)
            
            del img, img_final
            gc.collect()
            
        return rutas_optimizadas
    except Exception:
        return []

def generador_imagenes(rutas):
    for ruta in rutas:
        with Image.open(ruta) as img:
            yield img.convert('RGB')
        gc.collect()

# --- FLUJO PRINCIPAL ---

if not st.session_state.procesado:
    
    with st.sidebar:
        st.write("⚙️ **Configuración**")
        if st.button("🗑️ Borrar memoria de planillas", help="Usa esto para reiniciar la memoria al empezar una nueva quincena."):
            if os.path.exists(ARCHIVO_HISTORIAL):
                os.remove(ARCHIVO_HISTORIAL)
            st.success("¡Memoria borrada con éxito!")

    archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte masivo de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

    if archivo_zimbra is not None:
        if st.button("🚀 Procesar Planillas", type="primary"):
            
            reader = cargar_modelo_ocr()
            temp_dir = tempfile.mkdtemp()
            
            with st.spinner("📦 Enderezando y estandarizando en tamaño Carta (Letter)..."):
                adjuntos_totales = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
                
            if not adjuntos_totales:
                st.warning("No se encontraron planillas válidas.")
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                historial_actual = cargar_historial()
                adjuntos_nuevos = []
                nuevos_hashes = []
                
                for archivo in adjuntos_totales:
                    hash_doc = obtener_hash(archivo)
                    if hash_doc not in historial_actual:
                        adjuntos_nuevos.append(archivo)
                        nuevos_hashes.append(hash_doc)
                
                omitidas = len(adjuntos_totales) - len(adjuntos_nuevos)
                st.session_state.planillas_omitidas = omitidas

                if not adjuntos_nuevos:
                    st.info(f"Las {len(adjuntos_totales)} planillas de este paquete ya fueron procesadas anteriormente. No hay archivos nuevos para imprimir.")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                else:
                    progress_bar = st.progress(0)
                    todas_las_rutas_impresion = []
                    resultados_tabla = []
                    planillas_reales = 0 
                    
                    for i, archivo in enumerate(adjuntos_nuevos):
                        rutas_est = procesar_documento(archivo, reader, temp_dir)
                        
                        if rutas_est:
                            for idx, ruta in enumerate(rutas_est):
                                planillas_reales += 1
                                todas_las_rutas_impresion.append(ruta)
                                resultados_tabla.append({
                                    "No.": planillas_reales,
                                    "Documento Original": os.path.basename(archivo),
                                    "Estado": "Orientada y Estandarizada (Carta)"
                                })
                        
                        progress_bar.progress((i + 1) / len(adjuntos_nuevos))
                        gc.collect()
                    
                    for h in nuevos_hashes:
                        historial_actual.add(h)
                    guardar_historial(historial_actual)
                    
                    st.session_state.planillas_reales = planillas_reales
                    st.session_state.df_resultados = pd.DataFrame(resultados_tabla)

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

# --- VISTA DE RESULTADOS ---
if st.session_state.procesado:
    st.success(f"✅ ¡Se procesaron y estandarizaron {st.session_state.planillas_reales} planillas con éxito!")
    if st.session_state.planillas_omitidas > 0:
        st.info(f"⏭️ Se omitieron **{st.session_state.planillas_omitidas}** planillas ya procesadas en sesiones anteriores.")
    
    if not st.session_state.df_resultados.empty:
        st.dataframe(st.session_state.df_resultados, use_container_width=True, hide_index=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.session_state.pdf_bytes:
            st.download_button(
                label="🖨️ Descargar Archivo para Imprimir (Tamaño Carta)",
                data=st.session_state.pdf_bytes,
                file_name="Planillas_SERGEM_listasparaimprimir.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )
            
    st.write("---")
    if st.button("♻️ Subir nuevo archivo"):
        reiniciar_app()
