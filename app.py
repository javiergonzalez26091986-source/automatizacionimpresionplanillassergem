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
import pandas as pd

# --- PROTECCIÓN PARA IMÁGENES MASIVAS Y MEMORIA ---
Image.MAX_IMAGE_PIXELS = None 

# --- INICIALIZAR MEMORIA DE STREAMLIT ---
if "procesado" not in st.session_state:
    st.session_state.procesado = False
    st.session_state.pdf_bytes = None
    st.session_state.excel_bytes = None
    st.session_state.planillas_reales = 0
    st.session_state.df_resultados = pd.DataFrame()

def reiniciar_app():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Planillas SERGEM", layout="wide", page_icon="sergemLogo.ico")

# --- ESTILOS CSS (Para que se vea bien en cualquier navegador) ---
st.markdown("""
    <style>
    /* Fondo principal universal (Tema Claro) */
    .stApp {
        background-color: #f4f6f9 !important;
    }
    /* Hacer transparente el header de Streamlit */
    [data-testid="stHeader"] {
        background-color: rgba(244, 246, 249, 0) !important;
    }
    /* Forzar color de texto a oscuro para legibilidad */
    h1, h2, h3, p, span, label, .stMarkdown {
        color: #1e1e1e !important;
    }
    /* ARREGLO DEL BOTÓN DE CARGA DE ARCHIVOS */
    [data-testid="stFileUploadDropzone"] {
        background-color: #ffffff !important;
        border: 2px dashed #a0a0a5 !important;
        color: #1e1e1e !important;
    }
    [data-testid="stFileUploadDropzone"] * {
        color: #1e1e1e !important;
    }
    /* Estilo corporativo para los botones primarios */
    .stButton>button {
        background-color: #e63946 !important;
        color: #ffffff !important;
        border-radius: 8px !important;
        border: none !important;
        padding: 10px 24px !important;
        font-weight: bold !important;
    }
    .stButton>button:hover {
        background-color: #d62828 !important;
    }
    .stButton>button * {
        color: #ffffff !important;
    }
    /* Arreglo para la tabla de datos */
    [data-testid="stDataFrame"] {
        background-color: #ffffff !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- ENCABEZADO CON LOGO ---
col1, col2 = st.columns([1, 4])
with col1:
    if os.path.exists("sergemLogo.png"):
        st.image("sergemLogo.png", width=180)
    else:
        st.write("🏢 SERGEM")
with col2:
    st.title("Automatización de Planillas SERGEM")
    st.markdown("Orientación automática estricta, formato 100% apaisado y extracción de CENCO optimizada.")

# --- CARGA DEL MODELO OCR ---
@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- FUNCIONES DE LECTURA Y ORIENTACIÓN ---
def extraer_cenco_espacial(resultados):
    """Busca la palabra CENCO y captura el valor que está a su derecha"""
    cenco_val = "No detectado"
    cenco_y_center = None
    cenco_x_right = None
    
    # 1. Encontrar la coordenada de la palabra "CENCO"
    for bbox, texto, prob in resultados:
        txt_upper = str(texto).upper().strip()
        if "CENC" in txt_upper:
            # Si lo leyó todo en el mismo cuadro (ej: "CENCO: 416")
            limpio = re.sub(r'[^A-Z0-9]', '', txt_upper.replace('CENCO', '').replace('CENC', ''))
            if len(limpio) >= 2 and limpio not in ['OBS', 'PP', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'DESDES', 'CORTE']:
                return limpio
            
            # Guardamos las coordenadas para buscar a la derecha
            cenco_y_center = (bbox[0][1] + bbox[2][1]) / 2
            cenco_x_right = bbox[1][0]
            break
            
    # 2. Buscar números a la derecha en el mismo renglón
    if cenco_y_center is not None:
        candidatos = []
        for bbox, texto, prob in resultados:
            txt_upper = str(texto).upper().strip()
            if "CENC" in txt_upper: 
                continue
            
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            x_left = bbox[0][0]
            
            # Margen de error vertical (+/- 50 pixeles) y que esté a la derecha
            if abs(y_center - cenco_y_center) < 50 and x_left > cenco_x_right - 20:
                txt_clean = re.sub(r'[^A-Z0-9]', '', txt_upper)
                # Filtramos basura común para asegurar que sea un CENCO
                if len(txt_clean) >= 2 and txt_clean not in ['OBS', 'PP', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'FIRMA']:
                    candidatos.append((x_left, txt_clean))
        
        if candidatos:
            # Ordenamos por cercanía a la palabra CENCO
            candidatos.sort(key=lambda x: x[0])
            cenco_val = candidatos[0][1]
            
    return cenco_val

def orientar_y_leer(imagen_pil, reader):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # BRÚJULA PARA ENCONTRAR EL LADO CORRECTO
    img_brujula = imagen_pil.copy()
    img_brujula.thumbnail((800, 800), Image.Resampling.LANCZOS) # Más pequeño para ahorrar memoria
    img_brujula = img_brujula.convert('L')
    
    mejor_angulo = 0
    max_score = -1
    resultados_ganadores = []
    
    for angulo in [0, 90, 180, 270]:
        img_rotada = img_brujula.rotate(angulo, expand=True)
        img_np = np.array(img_rotada)
        res = reader.readtext(img_np)
        
        # Calificación basada en confianza del texto
        score = sum(len(texto) for bbox, texto, prob in res if prob > 0.45)
        
        if score > max_score:
            max_score = score
            mejor_angulo = angulo
            resultados_ganadores = res
            
        del img_np, img_rotada # Limpieza de memoria agresiva en cada ciclo
        gc.collect()
            
    del img_brujula
    gc.collect()

    # Rotar la imagen original de alta calidad
    imagen_pil = imagen_pil.rotate(mejor_angulo, expand=True)

    # EXTRACCIÓN ESPACIAL DEL CENCO
    cenco_final = extraer_cenco_espacial(resultados_ganadores)

    # EL TRUCO DEL LIENZO (Para forzar Horizontal sin distorsionar, rellenando con blanco)
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
            
            with st.spinner("📦 Analizando documentos optimizando la memoria RAM..."):
                adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
                
            if not adjuntos:
                st.warning("No se encontraron planillas válidas.")
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                progress_bar = st.progress(0)
                todas_las_rutas_impresion = []
                resultados_tabla = []
                planillas_reales = 0 
                
                for i, archivo in enumerate(adjuntos):
                    rutas_est, cencos = procesar_documento(archivo, reader, temp_dir)
                    
                    if rutas_est:
                        for idx, cenco in enumerate(cencos):
                            planillas_reales += 1
                            todas_las_rutas_impresion.append(rutas_est[idx])
                            resultados_tabla.append({
                                "No.": planillas_reales,
                                "Documento Original": os.path.basename(archivo),
                                "CENCO Detectado": cenco
                            })
                    
                    progress_bar.progress((i + 1) / len(adjuntos))
                    gc.collect()
                
                st.session_state.planillas_reales = planillas_reales
                st.session_state.df_resultados = pd.DataFrame(resultados_tabla)
                
                # Excel
                if not st.session_state.df_resultados.empty:
                    excel_buffer = io.BytesIO()
                    st.session_state.df_resultados.to_excel(excel_buffer, index=False)
                    st.session_state.excel_bytes = excel_buffer.getvalue()
                
                # PDF
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
    st.success(f"✅ ¡Se procesaron y alinearon {st.session_state.planillas_reales} planillas con éxito!")
    
    # Mostrar tabla directa, sin índices visuales para más limpieza
    if not st.session_state.df_resultados.empty:
        st.dataframe(st.session_state.df_resultados, use_container_width=True, hide_index=True)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.session_state.excel_bytes:
            st.download_button(
                label="📊 Descargar Reporte (Excel)",
                data=st.session_state.excel_bytes,
                file_name="Reporte_CENCOS.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
    with col2:
        if st.session_state.pdf_bytes:
            st.download_button(
                label="🖨️ Descargar Planillas Listas para Imprimir",
                data=st.session_state.pdf_bytes,
                file_name="Planillas_SERGEM_Listas.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )
            
    st.write("---")
    if st.button("♻️ Subir nuevo archivo"):
        reiniciar_app()
