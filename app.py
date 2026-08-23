import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import easyocr
import gc
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pdf2image import convert_from_path
import tempfile
import shutil
import pandas as pd
import re
import io

# --- PROTECCIÓN PARA IMÁGENES MASIVAS ---
Image.MAX_IMAGE_PIXELS = None 

# --- INICIALIZAR MEMORIA DE STREAMLIT ---
if "procesado" not in st.session_state:
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.excel_bytes = None
    st.session_state.df_resultados = pd.DataFrame()
    st.session_state.planillas_reales = 0

def reiniciar_app():
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.excel_bytes = None
    st.session_state.df_resultados = pd.DataFrame()
    st.session_state.planillas_reales = 0

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="sergemLogo.ico")

# --- ENCABEZADO CON LOGO ---
col1, col2 = st.columns([1, 4])
with col1:
    if os.path.exists("sergemLogo.png"):
        st.image("sergemLogo.png", width=180)
    else:
        st.write("🏢 SERGEM")
with col2:
    st.title("Automatización de Planillas SERGEM")
    st.markdown("Orientación automática perfecta y extracción directa de CENCO.")

# --- CARGA DEL MODELO OCR ---
@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- FUNCIONES DE LECTURA Y ORIENTACIÓN ---
def orientar_y_leer(imagen_pil, reader):
    # 1. Corregir rotación interna de celulares (EXIF)
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # 2. Crear miniatura en Blanco y Negro (SIN alteraciones de brillo/contraste)
    img_ocr_base = imagen_pil.copy()
    img_ocr_base.thumbnail((1200, 1200), Image.Resampling.LANCZOS) 
    img_ocr_base = img_ocr_base.convert('L') 

    # 3. Brújula Universal: Buscar el ángulo con MÁS texto legible (sea cual sea)
    mejor_angulo = 0
    max_palabras_validas = -1
    resultados_finales = []
    
    for angulo in [0, 90, 180, 270]:
        img_rotada = img_ocr_base.rotate(angulo, expand=True)
        img_np = np.array(img_rotada)
        resultados = reader.readtext(img_np)
        
        # Contar cualquier texto que parezca una palabra real (más de 2 caracteres alfanuméricos)
        palabras_validas = 0
        for bbox, texto, prob in resultados:
            texto_limpio = re.sub(r'[^a-zA-Z0-9]', '', texto)
            if len(texto_limpio) > 2:
                palabras_validas += 1
                
        if palabras_validas > max_palabras_validas:
            max_palabras_validas = palabras_validas
            mejor_angulo = angulo
            resultados_finales = resultados

    del img_ocr_base
    gc.collect()

    # 4. Rotar la imagen original de alta calidad al ángulo ganador
    imagen_pil = imagen_pil.rotate(mejor_angulo, expand=True)

    # 5. Extraer el CENCO (Filtro estricto para evitar alucinaciones)
    cenco_final = "No detectado"
    texto_ganador = " ".join([res[1].upper() for res in resultados_finales])
    
    # Busca explícitamente "CENCO" seguido de algo que parezca el código
    match = re.search(r'CENC[O0Q]?\s*[:\-\.]?\s*([A-Z0-9]{3,10})', texto_ganador)
    if match:
        posible_cenco = match.group(1).strip()
        # Evitar falsos positivos comunes
        if posible_cenco not in ['OBS', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'FIRMA']:
            cenco_final = posible_cenco

    # 6. Recortar marca de agua inferior (ej. CamScanner)
    ancho, alto = imagen_pil.size
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))

    # 7. Estampar CENCO visible
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
    
    # 8. GARANTÍA GEOMÉTRICA (Forzar Horizontal siempre)
    ancho_final, alto_final = imagen_pil.size
    if alto_final > ancho_final:
        imagen_pil = imagen_pil.rotate(90, expand=True)
    
    return imagen_pil, cenco_final

# --- EXTRACCIÓN MASIVA DE CORREOS Y ZIPs ---
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

def procesar_documento(ruta_archivo, reader, temp_dir):
    rutas_optimizadas = []
    resultados = []
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_archivo, dpi=130)
            for i, pagina in enumerate(paginas):
                pagina_rgb = pagina.convert('RGB')
                if pagina_rgb.size[0] < 500 or pagina_rgb.size[1] < 500: continue
                
                img_final, cenco = orientar_y_leer(pagina_rgb, reader)
                ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
                img_final.save(ruta_temp, 'JPEG', quality=85)
                rutas_optimizadas.append(ruta_temp)
                resultados.append(cenco)
                
                del pagina_rgb, img_final
                gc.collect()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            if img.size[0] < 500 or img.size[1] < 500: return [], []
            
            img_final, cenco = orientar_y_leer(img, reader)
            ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
            img_final.save(ruta_temp, 'JPEG', quality=85)
            rutas_optimizadas.append(ruta_temp)
            resultados.append(cenco)
            
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

# --- FLUJO PRINCIPAL ---

if not st.session_state.procesado:
    archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte masivo de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

    if archivo_zimbra is not None:
        if st.button("🚀 Procesar Quincena", type="primary"):
            
            reader = cargar_modelo_ocr()
            temp_dir = tempfile.mkdtemp()
            
            with st.spinner("📦 Extrayendo archivos y orientando planillas..."):
                adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
                
            if not adjuntos:
                st.warning("No se encontraron planillas válidas.")
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                resultados_tabla = []
                todas_las_rutas_impresion = []
                planillas_reales = 0 
                
                for i, archivo in enumerate(adjuntos):
                    nombre_archivo = os.path.basename(archivo)
                    status_text.text(f"Orientando y leyendo: {nombre_archivo}")
                    
                    rutas_est, datos_extraidos = procesar_documento(archivo, reader, temp_dir)
                    
                    if not rutas_est:
                        progress_bar.progress((i + 1) / len(adjuntos))
                        continue
                    
                    for idx, cenco in enumerate(datos_extraidos):
                        planillas_reales += 1
                        todas_las_rutas_impresion.append(rutas_est[idx])
                        
                        resultados_tabla.append({
                            "Documento": nombre_archivo,
                            "CENCO Extraído": cenco
                        })
                    
                    progress_bar.progress((i + 1) / len(adjuntos))
                    gc.collect()
                
                # --- GUARDAR RESULTADOS EN MEMORIA (STATE) ---
                st.session_state.df_resultados = pd.DataFrame(resultados_tabla)
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

# --- VISTA DE RESULTADOS ---
if st.session_state.procesado:
    st.success(f"✅ ¡Procesamiento finalizado! Se estructuraron {st.session_state.planillas_reales} planillas horizontales.")
    
    st.dataframe(st.session_state.df_resultados, use_container_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="📊 Descargar Reporte (Excel)",
            data=st.session_state.excel_bytes,
            file_name="Reporte_Quincenal_SERGEM.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )
    with col2:
        if st.session_state.pdf_bytes:
            st.download_button(
                label="🖨️ Descargar Planillas Listas para Imprimir",
                data=st.session_state.pdf_bytes,
                file_name="Planillas_SERGEM_Horizontales.pdf",
                mime="application/pdf",
                type="primary"
            )
            
    st.write("---")
    if st.button("♻️ Procesar Nuevo Archivo"):
        reiniciar_app()
        st.rerun()
