import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import easyocr
import gc
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
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
    st.session_state.df_resultados = pd.DataFrame()
    st.session_state.planillas_reales = 0

def reiniciar_app():
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
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
    st.markdown("Orientación automática perfecta, mejora de impresión y extracción rápida de CENCO.")

# --- CARGA DEL MODELO OCR ---
@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- FUNCIONES DE MEJORA Y LECTURA ---
def mejorar_imagen_para_impresion(imagen_pil):
    """Mejora la imagen solo para el PDF final (color y contraste) sin quemar el texto"""
    enhancer = ImageEnhance.Contrast(imagen_pil)
    imagen_pil = enhancer.enhance(1.15) # 15% más de contraste
    
    enhancer2 = ImageEnhance.Color(imagen_pil)
    imagen_pil = enhancer2.enhance(1.2) # 20% más de color/saturación
    
    return imagen_pil

def extraer_cenco_de_resultados(resultados):
    """Busca el CENCO en los resultados del OCR con alta precisión"""
    textos = [res[1].upper() for res in resultados]
    texto_completo = " ".join(textos)
    
    # 1. Búsqueda general
    match = re.search(r'CENC[O0Q]?\s*[:\-\.]?\s*([A-Z0-9]{2,8})', texto_completo)
    if match:
        cenco = match.group(1).strip()
        if cenco not in ['OBS', 'PP', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'ABP', 'ABR', 'ABE', 'FIRMA', 'PRODUCTO', 'DE']:
            return cenco
            
    # 2. Búsqueda por proximidad (si el escáner partió la palabra y el número en dos)
    for i, txt in enumerate(textos):
        if "CENC" in txt:
            # Revisar si el número quedó en el siguiente renglón
            if i + 1 < len(textos):
                posible_cenco = re.sub(r'[^A-Z0-9]', '', textos[i+1])
                if 2 <= len(posible_cenco) <= 8 and posible_cenco not in ['OBS', 'PP', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'FIRMA']:
                    return posible_cenco
                    
    return "No detectado"

def optimizar_y_leer(imagen_pil, reader):
    # 1. Corregir rotación interna de celulares (EXIF)
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # 2. FORZAR HORIZONTAL (Apaisado)
    ancho, alto = imagen_pil.size
    if alto > ancho:
        imagen_pil = imagen_pil.rotate(90, expand=True)

    # 3. Preparar imagen EXCLUSIVA para el OCR (Escala de grises, sin filtros que lo confundan)
    img_ocr = imagen_pil.convert('L')
    img_ocr.thumbnail((1600, 1600), Image.Resampling.LANCZOS) 
    
    # Leer texto
    img_np = np.array(img_ocr)
    resultados = reader.readtext(img_np)
    
    # 4. VERIFICAR ORIENTACIÓN (Saber si está de cabeza - 180 grados)
    y_coords_headers = []
    y_coords_footers = []
    
    for (bbox, texto, prob) in resultados:
        txt_upper = texto.upper()
        y_center = sum(p[1] for p in bbox) / 4
        # Palabras de la parte superior
        if any(kw in txt_upper for kw in ["SERGEM", "FORMATO", "REGISTRO", "PRESTACION", "CLIENTE"]):
            y_coords_headers.append(y_center)
        # Palabras de la parte inferior
        if any(kw in txt_upper for kw in ["TOTALES", "OBS", "PRODUCTO", "CAMBIOS"]):
            y_coords_footers.append(y_center)
            
    necesita_rotar = False
    alto_ocr = img_ocr.size[1]
    
    # Lógica de gravedad
    if y_coords_headers:
        avg_y = sum(y_coords_headers) / len(y_coords_headers)
        if avg_y > (alto_ocr / 2): # Si el encabezado está en la mitad de abajo
            necesita_rotar = True
    elif y_coords_footers:
        avg_y = sum(y_coords_footers) / len(y_coords_footers)
        if avg_y < (alto_ocr / 2): # Si el pie de página está en la mitad de arriba
            necesita_rotar = True
    elif len(resultados) < 10: 
        # Si casi no leyó texto, suele ser porque la imagen está totalmente al revés
        necesita_rotar = True

    # 5. Girar 180° si está de cabeza y volver a leer
    if necesita_rotar:
        imagen_pil = imagen_pil.rotate(180, expand=True)
        img_ocr = img_ocr.rotate(180, expand=True)
        del img_np
        gc.collect()
        
        img_np = np.array(img_ocr)
        resultados = reader.readtext(img_np)

    # 6. Extraer el CENCO
    cenco_final = extraer_cenco_de_resultados(resultados)
    
    del img_np, img_ocr
    gc.collect()

    # 7. MEJORAR IMAGEN PARA IMPRESIÓN (Ahora sí aplicamos el filtro)
    imagen_pil = mejorar_imagen_para_impresion(imagen_pil)

    # 8. Recorte del borde inferior (CamScanner)
    ancho, alto = imagen_pil.size
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
    
    # 9. Estampar el CENCO
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
                
                img_final, cenco = optimizar_y_leer(pagina_rgb, reader)
                ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
                img_final.save(ruta_temp, 'JPEG', quality=85)
                rutas_optimizadas.append(ruta_temp)
                resultados.append(cenco)
                
                del pagina_rgb, img_final
                gc.collect()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            if img.size[0] < 500 or img.size[1] < 500: return [], []
            
            img_final, cenco = optimizar_y_leer(img, reader)
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

# --- FLUJO PRINCIPAL DE INTERFAZ ---

if not st.session_state.procesado:
    archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte masivo de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

    if archivo_zimbra is not None:
        if st.button("🚀 Procesar Quincena", type="primary"):
            
            reader = cargar_modelo_ocr()
            temp_dir = tempfile.mkdtemp()
            
            with st.spinner("📦 Extrayendo archivos, enderezando y aplicando OCR..."):
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
    st.success(f"✅ ¡Procesamiento masivo finalizado! Se orientaron al derecho {st.session_state.planillas_reales} planillas con éxito.")
    
    st.dataframe(st.session_state.df_resultados, use_container_width=True)
    
    if st.session_state.pdf_bytes:
        st.download_button(
            label="🖨️ Descargar Todas las Planillas (Apaisadas y al derecho)",
            data=st.session_state.pdf_bytes,
            file_name="Planillas_SERGEM_Alineadas.pdf",
            mime="application/pdf",
            type="primary"
        )
            
    st.write("---")
    if st.button("♻️ Finalizar y Procesar Nueva Quincena"):
        reiniciar_app()
        st.rerun()
