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

# --- PROTECCIÓN Y BLINDAJE DE MEMORIA ---
Image.MAX_IMAGE_PIXELS = None 

if "procesado" not in st.session_state:
    st.session_state.procesado = False
    st.session_state.pdf_path = None
    st.session_state.planillas_reales = 0
    st.session_state.resumen = []

def reiniciar_app():
    st.session_state.procesado = False
    st.session_state.pdf_path = None
    st.session_state.planillas_reales = 0
    st.session_state.resumen = []

# --- CONFIGURACIÓN DE PÁGINA E INTERFAZ ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="sergemLogo.ico")

# --- CSS PERSONALIZADO (FONDO GRIS Y DISEÑO PROFESIONAL) ---
st.markdown("""
    <style>
    .stApp {
        background-color: #e4e8ec;
    }
    .stApp, .stApp p, .stApp h1, .stApp h2, .stApp h3 {
        color: #1a1c1e;
    }
    .summary-box {
        background-color: #ffffff;
        padding: 12px;
        border-radius: 6px;
        border-left: 5px solid #2e7bcf;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 8px;
        color: #333333;
        font-weight: 500;
    }
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
    st.markdown("Procesamiento de alto rendimiento, orientación geométrica y lectura espacial estricta.")

# --- CARGA DEL MODELO OCR ---
@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- LECTURA ESPACIAL Y ORIENTACIÓN ---
def orientar_y_leer(imagen_pil, reader):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # 1. BRÚJULA (Rápida y de bajo consumo)
    img_brujula = imagen_pil.copy()
    img_brujula.thumbnail((800, 800), Image.Resampling.LANCZOS)
    img_brujula = img_brujula.convert('L')
    
    mejor_angulo = 0
    max_score = -1
    
    for angulo in [0, 90, 180, 270]:
        img_rotada = img_brujula.rotate(angulo, expand=True)
        # detail=0 usa mínima memoria
        textos_temp = reader.readtext(np.array(img_rotada), detail=0)
        score = sum(len(txt) for txt in textos_temp if len(txt) > 2)
        
        if score > max_score:
            max_score = score
            mejor_angulo = angulo
            
    del img_brujula
    gc.collect()

    # 2. ROTAR IMAGEN FINAL
    imagen_pil = imagen_pil.rotate(mejor_angulo, expand=True)

    # 3. LECTURA ESPACIAL EXACTA (Sobre la imagen ya derecha)
    img_ocr = imagen_pil.convert('L')
    resultados = reader.readtext(np.array(img_ocr))
    
    cenco_final = "No detectado"
    cajas_cenco = []
    
    # Buscar las coordenadas de la palabra CENCO
    for bbox, text, prob in resultados:
        if "CENC" in text.upper():
            cajas_cenco.append(bbox)
            
    # Trazar línea a la derecha de la palabra
    for cbox in cajas_cenco:
        c_y_center = (cbox[0][1] + cbox[2][1]) / 2
        c_x_right = max(cbox[1][0], cbox[2][0])
        c_height = abs(cbox[2][1] - cbox[0][1])
        
        posibles = []
        for bbox, text, prob in resultados:
            text_limpio = re.sub(r'[^A-Z0-9]', '', text.upper())
            if not text_limpio or "CENC" in text.upper(): continue
            
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            x_left = min(bbox[0][0], bbox[3][0])
            
            # Condición Espacial: A la derecha (tolerancia) y en la misma altura
            if x_left > c_x_right - 15 and abs(y_center - c_y_center) < (c_height * 1.5):
                posibles.append((x_left, text_limpio))
                
        if posibles:
            posibles.sort(key=lambda x: x[0]) # El más cercano físicamente a la derecha
            candidato = posibles[0][1]
            if candidato not in ['OBS', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'CORTE', 'DESDE', 'HASTA']:
                cenco_final = candidato
                break

    del img_ocr
    gc.collect()

    # 4. EL TRUCO DEL LIENZO (Forzar Horizontal)
    ancho, alto = imagen_pil.size
    if alto > ancho:
        nuevo_ancho = int(alto * 1.3)
        canvas = Image.new('RGB', (nuevo_ancho, alto), 'white')
        offset_x = (nuevo_ancho - ancho) // 2
        canvas.paste(imagen_pil, (offset_x, 0))
        imagen_pil = canvas

    # 5. RECORTAR Y ESTAMPAR
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

# --- EXTRACCIÓN Y PROCESAMIENTO MASIVO ---
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
        except Exception: pass
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
                # Escalar para ahorrar peso en el PDF final (Resolución apta para impresión A4)
                img_final.thumbnail((1754, 1754), Image.Resampling.LANCZOS)
                
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
            img_final.thumbnail((1754, 1754), Image.Resampling.LANCZOS)
            
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
                            resumen.append(f"📄 Documento {planillas_reales} procesado exitosamente | CENCO Detectado: {cenco}")
                    
                    progress_bar.progress((i + 1) / len(adjuntos))
                    gc.collect()
                
                st.session_state.planillas_reales = planillas_reales
                st.session_state.resumen = resumen
                
                if todas_las_rutas_impresion:
                    # BLINDAJE DE MEMORIA: Guardar PDF directamente en disco, no en RAM.
                    ruta_pdf_final = os.path.join(temp_dir, "Planillas_SERGEM_Listas.pdf")
                    with Image.open(todas_las_rutas_impresion[0]) as primera_img:
                        primera_img_rgb = primera_img.convert('RGB')
                        if len(todas_las_rutas_impresion) > 1:
                            primera_img_rgb.save(ruta_pdf_final, format="PDF", save_all=True, append_images=generador_imagenes(todas_las_rutas_impresion[1:]))
                        else:
                            primera_img_rgb.save(ruta_pdf_final, format="PDF")
                            
                    st.session_state.pdf_path = ruta_pdf_final
                
                st.session_state.procesado = True
                st.rerun()

# --- VISTA DE RESULTADOS (DISEÑO LIMPIO) ---
if st.session_state.procesado:
    st.success(f"✅ ¡Se procesaron y alinearon {st.session_state.planillas_reales} planillas!")
    
    st.markdown("### 📋 Resumen de Procesamiento")
    for linea in st.session_state.resumen:
        st.markdown(f"<div class='summary-box'>{linea}</div>", unsafe_allow_html=True)
    
    st.write("---")
    
    if st.session_state.pdf_path and os.path.exists(st.session_state.pdf_path):
        with open(st.session_state.pdf_path, "rb") as pdf_file:
            st.download_button(
                label="🖨️ Descargar Archivo para Imprimir",
                data=pdf_file,
                file_name="Planillas_SERGEM_Listas.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )
            
    st.write("---")
    if st.button("♻️ Subir nuevo archivo"):
        if st.session_state.pdf_path:
            shutil.rmtree(os.path.dirname(st.session_state.pdf_path), ignore_errors=True)
        reiniciar_app()
        st.rerun()
