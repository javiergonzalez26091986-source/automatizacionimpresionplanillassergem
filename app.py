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
import re
import io

# --- PROTECCIÓN PARA IMÁGENES MASIVAS ---
Image.MAX_IMAGE_PIXELS = None 

# --- INICIALIZAR MEMORIA DE STREAMLIT ---
if "procesado" not in st.session_state:
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.planillas_reales = 0
    st.session_state.resumen = []

def reiniciar_app():
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.planillas_reales = 0
    st.session_state.resumen = []

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="🖨️")

col1, col2 = st.columns([1, 4])
with col1:
    st.write("🏢 SERGEM")
with col2:
    st.title("Automatización de Planillas SERGEM")
    st.markdown("Orientación automática estricta y formato 100% apaisado.")

# --- CARGA DEL MODELO OCR ---
@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- FUNCIONES DE LECTURA Y ORIENTACIÓN ---
def orientar_y_leer(imagen_pil, reader):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    img_brujula = imagen_pil.copy()
    img_brujula.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    img_brujula = img_brujula.convert('L')
    
    mejor_angulo = 0
    max_score = -1
    resultados_ganadores = []
    
    # BRÚJULA DE CONFIANZA MEJORADA
    for angulo in [0, 90, 180, 270]:
        img_rotada = img_brujula.rotate(angulo, expand=True)
        res = reader.readtext(np.array(img_rotada))
        
        score = sum(len(texto) for bbox, texto, prob in res if prob > 0.45)
        
        if score > max_score:
            max_score = score
            mejor_angulo = angulo
            resultados_ganadores = res
            
    del img_brujula
    gc.collect()

    # Rotar la imagen original
    imagen_pil = imagen_pil.rotate(mejor_angulo, expand=True)

    # EXTRACCIÓN DE CENCO (Filtro estricto)
    cenco_final = "No detectado"
    textos_confiables = [texto.upper() for bbox, texto, prob in resultados_ganadores if prob > 0.3]
    
    for i, txt in enumerate(textos_confiables):
        if "CENC" in txt or "CEN" in txt:
            match = re.search(r'\d{3,10}|[A-Z]\d{3}', txt)
            if match:
                cenco_final = match.group()
            elif i + 1 < len(textos_confiables):
                siguiente = re.sub(r'[^A-Z0-9]', '', textos_confiables[i+1])
                # Filtro Anti-Alucinaciones de fechas
                if len(siguiente) >= 3 and siguiente not in ['10115', '11631', '0115', '1631', 'OBS', 'MES', 'ANO', 'AÑO']:
                    cenco_final = siguiente
            break

    # EL TRUCO DEL LIENZO (Forzar Horizontal siempre)
    ancho, alto = imagen_pil.size
    if alto > ancho:
        nuevo_ancho = int(alto * 1.3)
        canvas = Image.new('RGB', (nuevo_ancho, alto), 'white')
        offset_x = (nuevo_ancho - ancho) // 2
        canvas.paste(imagen_pil, (offset_x, 0))
        imagen_pil = canvas

    # Recortar marca de agua y estampar
    ancho_final, alto_final = imagen_pil.size
    recorte_inferior = int(alto_final * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho_final, alto_final - recorte_inferior))

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
    cencos_extraidos = []
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
                cencos_extraidos.append(cenco)
                
                del pagina_rgb, img_final
                gc.collect()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            if img.size[0] < 500 or img.size[1] < 500: return [], []
            
            img_final, cenco = orientar_y_leer(img, reader)
            ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
            img_final.save(ruta_temp, 'JPEG', quality=85)
            rutas_optimizadas.append(ruta_temp)
            cencos_extraidos.append(cenco)
            
            del img, img_final
            gc.collect()
            
        return rutas_optimizadas, cencos_extraidos
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
        if st.button("🚀 Procesar Planillas", type="primary"):
            
            reader = cargar_modelo_ocr()
            temp_dir = tempfile.mkdtemp()
            
            with st.spinner("📦 Analizando documentos..."):
                adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
                
            if not adjuntos:
                st.warning("No se encontraron planillas válidas.")
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                progress_bar = st.progress(0)
                todas_las_rutas_impresion = []
                resumen = []
                planillas_reales = 0 
                
                for i, archivo in enumerate(adjuntos):
                    rutas_est, cencos = procesar_documento(archivo, reader, temp_dir)
                    
                    if rutas_est:
                        for idx, cenco in enumerate(cencos):
                            planillas_reales += 1
                            todas_las_rutas_impresion.append(rutas_est[idx])
                            resumen.append(f"Documento {planillas_reales}: CENCO {cenco}")
                    
                    progress_bar.progress((i + 1) / len(adjuntos))
                    gc.collect()
                
                st.session_state.planillas_reales = planillas_reales
                st.session_state.resumen = resumen
                
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
    st.success(f"✅ ¡Se procesaron y alinearon {st.session_state.planillas_reales} planillas!")
    
    with st.expander("Ver detalle de CENCOs detectados"):
        for linea in st.session_state.resumen:
            st.text(linea)
    
    if st.session_state.pdf_bytes:
        st.download_button(
            label="🖨️ Descargar Archivo para Imprimir",
            data=st.session_state.pdf_bytes,
            file_name="Planillas_SERGEM_Listas.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True
        )
            
    st.write("---")
    if st.button("♻️ Subir nuevo archivo"):
        reiniciar_app()
        st.rerun()
