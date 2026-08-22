import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import easyocr
import cv2
import numpy as np
from PIL import Image
import tempfile
import shutil

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="🖨️")
st.title("🖨️ Automatización de Planillas SERGEM")
st.markdown("Sube el archivo exportado de Zimbra (.tgz o .zip) para procesar las planillas.")

# --- FUNCIONES DE PROCESAMIENTO ---

def extraer_adjuntos_de_eml(contenido_eml, directorio_salida):
    """Lee un archivo .eml y guarda sus adjuntos (PDFs o Imágenes)"""
    adjuntos = []
    msg = email.message_from_bytes(contenido_eml, policy=policy.default)
    
    for part in msg.walk():
        # Ignorar si es multipart o si no es un adjunto
        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
            continue
            
        filename = part.get_filename()
        if filename:
            # Filtrar solo pdfs o imágenes comunes
            if filename.lower().endswith(('.pdf', '.jpeg', '.jpg', '.png')):
                ruta_archivo = os.path.join(directorio_salida, filename)
                # Manejar nombres duplicados
                contador = 1
                while os.path.exists(ruta_archivo):
                    nombre_base, ext = os.path.splitext(filename)
                    ruta_archivo = os.path.join(directorio_salida, f"{nombre_base}_{contador}{ext}")
                    contador += 1
                    
                with open(ruta_archivo, 'wb') as new_file:
                    new_file.write(part.get_payload(decode=True))
                adjuntos.append(ruta_archivo)
    return adjuntos

def procesar_archivo_comprimido(archivo_subido, directorio_salida):
    """Descomprime el archivo subido y extrae los adjuntos de los .eml"""
    todos_los_adjuntos = []
    
    # Manejar archivo .tgz
    if archivo_subido.name.endswith('.tgz') or archivo_subido.name.endswith('.tar.gz'):
        with tarfile.open(fileobj=archivo_subido, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".eml"):
                    f = tar.extractfile(member)
                    if f is not None:
                        adjuntos = extraer_adjuntos_de_eml(f.read(), directorio_salida)
                        todos_los_adjuntos.extend(adjuntos)
                        
    # Manejar archivo .zip (por si acaso Zimbra lo exporta así)
    elif archivo_subido.name.endswith('.zip'):
        with zipfile.ZipFile(archivo_subido, 'r') as zip_ref:
            for filename in zip_ref.namelist():
                if filename.endswith(".eml"):
                    with zip_ref.open(filename) as f:
                        adjuntos = extraer_adjuntos_de_eml(f.read(), directorio_salida)
                        todos_los_adjuntos.extend(adjuntos)
                        
    return todos_los_adjuntos

@st.cache_resource
def cargar_modelo_ocr():
    # Inicializar EasyOCR solo una vez para optimizar memoria
    return easyocr.Reader(['es'])

def leer_cenco_easyocr(ruta_imagen, reader):
    """Aplica OCR a la imagen buscando la palabra CENCO"""
    try:
        resultados = reader.readtext(ruta_imagen)
        for (bbox, texto, prob) in resultados:
            # Buscar variaciones comunes por la mala calidad de escaneo
            if "CENCO" in texto.upper() or "CENC" in texto.upper():
                return texto
        return "No detectado"
    except Exception as e:
        return f"Error leyendo: {e}"

# --- INTERFAZ PRINCIPAL ---

archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

if archivo_zimbra is not None:
    if st.button("🚀 Procesar Archivo", type="primary"):
        reader = cargar_modelo_ocr()
        
        # Crear directorio temporal para los archivos
        temp_dir = tempfile.mkdtemp()
        
        with st.spinner("📦 Descomprimiendo y extrayendo planillas de los correos..."):
            adjuntos_extraidos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
            
        if not adjuntos_extraidos:
            st.warning("No se encontraron planillas adjuntas en los correos de este archivo.")
        else:
            st.success(f"✅ Se extrajeron {len(adjuntos_extraidos)} planillas con éxito.")
            
            st.write("### 🔍 Análisis de Planillas")
            
            # Mostrar progreso de OCR
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            resultados = []
            
            for i, archivo in enumerate(adjuntos_extraidos):
                nombre_archivo = os.path.basename(archivo)
                status_text.text(f"Analizando: {nombre_archivo}")
                
                # Por ahora, solo pasamos OCR si es imagen. Los PDF requieren conversión a imagen primero.
                cenco_texto = "Pendiente procesar PDF a Imagen"
                if archivo.lower().endswith(('.png', '.jpg', '.jpeg')):
                     cenco_texto = leer_cenco_easyocr(archivo, reader)
                
                resultados.append({
                    "Archivo": nombre_archivo,
                    "CENCO Detectado": cenco_texto
                })
                
                progress_bar.progress((i + 1) / len(adjuntos_extraidos))
            
            status_text.text("Análisis completado.")
            
            # Mostrar resultados en tabla
            st.dataframe(resultados, use_container_width=True)
            
        # Limpiar directorio temporal después del proceso
        shutil.rmtree(temp_dir)
