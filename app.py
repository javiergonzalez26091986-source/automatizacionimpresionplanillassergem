import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import easyocr
import gc
from PIL import Image
from pdf2image import convert_from_path
import tempfile
import shutil

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="🖨️")
st.title("🖨️ Automatización de Planillas SERGEM")
st.markdown("Sube el archivo exportado de Zimbra (.tgz o .zip) para procesar las planillas.")

# --- FUNCIONES DE PROCESAMIENTO ---
def extraer_adjuntos_de_eml(contenido_eml, directorio_salida):
    adjuntos = []
    msg = email.message_from_bytes(contenido_eml, policy=policy.default)
    
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
            continue
            
        filename = part.get_filename()
        if filename:
            if filename.lower().endswith(('.pdf', '.jpeg', '.jpg', '.png')):
                ruta_archivo = os.path.join(directorio_salida, filename)
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
    todos_los_adjuntos = []
    if archivo_subido.name.endswith('.tgz') or archivo_subido.name.endswith('.tar.gz'):
        with tarfile.open(fileobj=archivo_subido, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".eml"):
                    f = tar.extractfile(member)
                    if f is not None:
                        adjuntos = extraer_adjuntos_de_eml(f.read(), directorio_salida)
                        todos_los_adjuntos.extend(adjuntos)
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
    # Cargar solo cuando se necesite y mantener en caché
    return easyocr.Reader(['es'])

def leer_cenco_easyocr(ruta_imagen_o_pdf, reader, temp_dir):
    """Convierte PDF a imagen si es necesario y aplica OCR buscando CENCO"""
    try:
        imagenes_a_procesar = []
        
        # Si es un PDF, lo convertimos a imágenes y las guardamos en el temp_dir principal
        if ruta_imagen_o_pdf.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_imagen_o_pdf, dpi=150) # DPI bajado a 150 para ahorrar memoria
            for i, pagina in enumerate(paginas):
                nombre_temp = os.path.join(temp_dir, f"pagina_temp_{i}_{os.urandom(4).hex()}.jpg")
                pagina.save(nombre_temp, 'JPEG')
                imagenes_a_procesar.append(nombre_temp)
        else:
            imagenes_a_procesar.append(ruta_imagen_o_pdf)
            
        cencos_encontrados = []
        for img_path in imagenes_a_procesar:
            resultados = reader.readtext(img_path)
            for (bbox, texto, prob) in resultados:
                if "CENCO" in texto.upper() or "CENC" in texto.upper():
                    cencos_encontrados.append(texto)
                    
        # Liberar memoria de imágenes procesadas
        del imagenes_a_procesar
        gc.collect() 
        
        if cencos_encontrados:
            return ", ".join(cencos_encontrados)
        return "No detectado"
    except Exception as e:
        return f"Error: {e}"

# --- INTERFAZ PRINCIPAL ---
archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

if archivo_zimbra is not None:
    if st.button("🚀 Procesar Archivo", type="primary"):
        reader = cargar_modelo_ocr()
        temp_dir = tempfile.mkdtemp()
        
        with st.spinner("📦 Descomprimiendo y extrayendo planillas de los correos..."):
            adjuntos_extraidos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
            
        if not adjuntos_extraidos:
            st.warning("No se encontraron planillas adjuntas en los correos de este archivo.")
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            st.success(f"✅ Se extrajeron {len(adjuntos_extraidos)} planillas con éxito.")
            st.write("### 🔍 Análisis y Lectura OCR de Planillas")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados = []
            
            for i, archivo in enumerate(adjuntos_extraidos):
                nombre_archivo = os.path.basename(archivo)
                status_text.text(f"Analizando: {nombre_archivo}")
                
                # Pasamos el temp_dir para que las imágenes del PDF se guarden allí y se borren al final
                cenco_texto = leer_cenco_easyocr(archivo, reader, temp_dir)
                
                resultados.append({
                    "Archivo": nombre_archivo,
                    "CENCO Detectado": cenco_texto
                })
                
                progress_bar.progress((i + 1) / len(adjuntos_extraidos))
            
            status_text.text("¡Análisis completado con éxito!")
            st.dataframe(resultados, use_container_width=True)
            
            # Limpieza final de todo el directorio temporal
            shutil.rmtree(temp_dir, ignore_errors=True)
            gc.collect()
