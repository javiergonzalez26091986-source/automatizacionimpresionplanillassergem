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
import pandas as pd
import re

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="🖨️")
st.title("🖨️ Automatización de Planillas SERGEM")
st.markdown("Sistema integral de lectura OCR, cruce de datos y consolidación para impresión en bloque.")

# --- CARGA DE DATOS Y MODELOS ---
@st.cache_data
def cargar_base_datos(ruta_archivo="centrosDeCostos.xlsx"):
    try:
        df = pd.read_excel(ruta_archivo)
        # Limpiamos y convertimos a texto para asegurar coincidencias exactas
        df['CENTRO_COSTO'] = df['CENTRO_COSTO'].astype(str).str.strip()
        return df
    except Exception as e:
        return pd.DataFrame(columns=['EMPRESA', 'CENTRO_COSTO'])

@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- EXTRACCIÓN DE ADJUNTOS ---
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
    if archivo_subido.name.endswith(('.tgz', '.tar.gz')):
        with tarfile.open(fileobj=archivo_subido, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".eml"):
                    f = tar.extractfile(member)
                    if f is not None:
                        todos_los_adjuntos.extend(extraer_adjuntos_de_eml(f.read(), directorio_salida))
    elif archivo_subido.name.endswith('.zip'):
        with zipfile.ZipFile(archivo_subido, 'r') as zip_ref:
            for filename in zip_ref.namelist():
                if filename.endswith(".eml"):
                    with zip_ref.open(filename) as f:
                        todos_los_adjuntos.extend(extraer_adjuntos_de_eml(f.read(), directorio_salida))
    return todos_los_adjuntos

# --- PROCESAMIENTO OCR Y UNIFICACIÓN ---
def extraer_numero_cenco(resultados):
    """Busca la palabra CENCO y extrae el código alfanumérico asociado"""
    texto_completo = " ".join([texto for (bbox, texto, prob) in resultados]).upper()
    # Captura variaciones de CENCO seguido del número o código
    match = re.search(r'CENC[O0]?\s*[:\-\.]?\s*([A-Z0-9]+)', texto_completo)
    if match:
        return match.group(1)
    return None

def analizar_y_estandarizar(ruta_archivo, reader, temp_dir):
    """Convierte, aplica OCR y retorna las imágenes listas para el PDF maestro."""
    imagenes_pil = []
    cencos_detectados = []
    
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_archivo, dpi=150)
            for i, pagina in enumerate(paginas):
                img_path = os.path.join(temp_dir, f"temp_ocr_{os.urandom(4).hex()}.jpg")
                pagina_rgb = pagina.convert('RGB')
                pagina_rgb.save(img_path, 'JPEG')
                imagenes_pil.append(pagina_rgb)
                
                resultados = reader.readtext(img_path)
                cenco = extraer_numero_cenco(resultados)
                if cenco: cencos_detectados.append(cenco)
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            imagenes_pil.append(img)
            resultados = reader.readtext(ruta_archivo)
            cenco = extraer_numero_cenco(resultados)
            if cenco: cencos_detectados.append(cenco)
            
        cenco_final = cencos_detectados[0] if cencos_detectados else None
        return cenco_final, imagenes_pil
    except Exception as e:
        return None, []

# --- INTERFAZ PRINCIPAL ---
df_base = cargar_base_datos()
reader = cargar_modelo_ocr()

archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

if archivo_zimbra is not None:
    if st.button("🚀 Procesar Archivo y Generar Bloque de Impresión", type="primary"):
        temp_dir = tempfile.mkdtemp()
        
        with st.spinner("📦 Descomprimiendo y analizando planillas masivamente..."):
            adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
            
        if not adjuntos:
            st.warning("No se encontraron planillas adjuntas en los correos de este archivo.")
        else:
            st.success(f"✅ Se analizaron {len(adjuntos)} documentos con éxito.")
            st.write("### 🔍 Resultados del Análisis y Cruce de Datos")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados_tabla = []
            todas_las_imagenes_impresion = []
            alertas = 0
            
            for i, archivo in enumerate(adjuntos):
                nombre_archivo = os.path.basename(archivo)
                status_text.text(f"Analizando OCR y cruzando datos: {nombre_archivo}")
                
                cenco_detectado, imagenes_estandarizadas = analizar_y_estandarizar(archivo, reader, temp_dir)
                todas_las_imagenes_impresion.extend(imagenes_estandarizadas)
                
                # --- Cruce Lógico con Base de Datos ---
                empresa_asignada = "No asignada (Validar)"
                if cenco_detectado:
                    coincidencia = df_base[df_base['CENTRO_COSTO'] == cenco_detectado]
                    if not coincidencia.empty:
                        empresa_asignada = coincidencia.iloc[0]['EMPRESA']
                    else:
                        empresa_asignada = "CENCO Nuevo/No registrado"
                else:
                    alertas += 1
                
                resultados_tabla.append({
                    "Documento": nombre_archivo,
                    "CENCO Extraído": cenco_detectado if cenco_detectado else "⚠️ VACÍO",
                    "Cliente Asignado": empresa_asignada
                })
                
                progress_bar.progress((i + 1) / len(adjuntos))
                gc.collect()
            
            status_text.text("¡Procesamiento completado!")
            
            # --- MANEJO DE ALERTAS ---
            if alertas > 0:
                st.error(f"⚠️ Atención Doña Yesenia: Hay {alertas} planilla(s) sin un número CENCO detectado. Revise las marcadas como 'VACÍO' en la tabla inferior.")
            
            # --- MOSTRAR TABLA ---
            df_resultados = pd.DataFrame(resultados_tabla)
            # Resaltado en Streamlit de las filas con Alertas
            st.dataframe(
                df_resultados, 
                use_container_width=True
            )
            
            # --- GENERACIÓN DE IMPRESIÓN EN BLOQUE (EL ARCHIVO MÁGICO) ---
            if todas_las_imagenes_impresion:
                ruta_pdf_final = os.path.join(temp_dir, "Planillas_Listas_Para_Imprimir.pdf")
                # Pilllow guarda nativamente la lista de imágenes como un solo PDF
                todas_las_imagenes_impresion[0].save(
                    ruta_pdf_final, 
                    save_all=True, 
                    append_images=todas_las_imagenes_impresion[1:]
                )
                
                st.write("---")
                st.write("### 🖨️ Documento Consolidado de Impresión")
                st.info("Todos los correos, fotos y PDFs se han unificado en un único archivo maestro. No es necesario ajustar márgenes; descarga el archivo e imprímelo en bloque.")
                
                with open(ruta_pdf_final, "rb") as pdf_file:
                    st.download_button(
                        label="📄 Descargar Todas las Planillas (PDF Único)",
                        data=pdf_file,
                        file_name="Planillas_SERGEM_Unificadas.pdf",
                        mime="application/pdf",
                        type="primary"
                    )
            
        # Limpieza de memoria y discos para evitar la caída del servidor
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()
