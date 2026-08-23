import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import gc
import numpy as np
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

# --- PROTECCIÓN DE PIL PARA IMÁGENES PESADAS ---
Image.MAX_IMAGE_PIXELS = None 

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
    st.markdown("Procesamiento quincenal masivo con IA Generativa, deducción de clientes y blindaje de CPU.")

# --- CARGA DE DATOS ---
@st.cache_data
def cargar_base_datos(ruta_archivo="centrosDeCostos.xlsx"):
    try:
        df = pd.read_excel(ruta_archivo)
        df['CENTRO_COSTO'] = df['CENTRO_COSTO'].astype(str).str.strip()
        df['EMPRESA'] = df['EMPRESA'].astype(str).str.strip()
        return df
    except Exception as e:
        return pd.DataFrame(columns=['EMPRESA', 'CENTRO_COSTO'])

# --- FUNCIONES DE IA Y PROCESAMIENTO ---
def analizar_con_gemini(imagen_pil):
    img_api = imagen_pil.copy()
    img_api.thumbnail((1200, 1200), Image.Resampling.LANCZOS) # Miniatura ultraligera para la API
    
    prompt = """
    Eres un asistente experto en lectura de documentos. Analiza esta planilla.
    Devuelve estrictamente un objeto JSON con estas claves:
    {
        "NOMBRE": "Nombre del colaborador o conductor",
        "CLIENTE": "Nombre del cliente (ej. Covacrans, Exito, Olimpica, etc.)",
        "SUCURSAL": "Ciudad o sucursal. Si no hay, pon 'No detectado'",
        "CENCO": "Número del centro de costo (solo el número o código). Si está vacío o ilegible, devuelve 'No detectado'",
        "ENCABEZADO_ESTA_EN": "Indica dónde está visualmente el logo de SERGEM y el título en la imagen recibida. Opciones: 'arriba', 'abajo', 'izquierda', 'derecha'"
    }
    """
    
    for intento in range(4): # Reintentos anti-colapso
        try:
            response = model.generate_content([prompt, img_api])
            texto_respuesta = response.text
            
            # Limpieza del formato Markdown de la respuesta
            if "```json" in texto_respuesta:
                texto_respuesta = texto_respuesta.split("```json")[1].split("```")[0]
            elif "```" in texto_respuesta:
                texto_respuesta = texto_respuesta.split("```")[1].split("```")[0]
                
            return json.loads(texto_respuesta.strip())
        except Exception as e:
            time.sleep(3) # Pausa de seguridad si Google nos limita por enviar muchas fotos rápido
            
    return {"NOMBRE": "No detectado", "CLIENTE": "No detectado", "SUCURSAL": "No detectado", "CENCO": "No detectado", "ENCABEZADO_ESTA_EN": "arriba"}

def cruzar_datos_inteligente(cenco_ocr, cliente_ocr, df_base):
    cenco_final = str(cenco_ocr).strip()
    empresa_asignada = "No asignada (Validar)"
    
    # 1. Intento por CENCO
    if cenco_final and cenco_final.upper() not in ["NO DETECTADO", "NULL", "NONE", "VACÍO", ""]:
        coincidencia = df_base[df_base['CENTRO_COSTO'] == cenco_final]
        if not coincidencia.empty:
            return cenco_final, coincidencia.iloc[0]['EMPRESA']
            
    # 2. Intento de Deducción por Cliente (Fuzzy Match)
    cenco_final = "No detectado"
    if cliente_ocr and cliente_ocr.upper() != "NO DETECTADO":
        empresas_bd = df_base['EMPRESA'].unique().tolist()
        mejores = difflib.get_close_matches(cliente_ocr.upper(), [e.upper() for e in empresas_bd], n=1, cutoff=0.5)
        
        if mejores:
            emp_match = mejores[0]
            empresa_asignada = df_base[df_base['EMPRESA'].str.upper() == emp_match].iloc[0]['EMPRESA']
            cenco_final = df_base[df_base['EMPRESA'] == empresa_asignada].iloc[0]['CENTRO_COSTO']
            empresa_asignada = f"{empresa_asignada} (Deducido)"
            
    return str(cenco_final), empresa_asignada

def optimizar_y_leer(imagen_pil, df_base):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    datos_ia = analizar_con_gemini(imagen_pil)
    
    # --- ORIENTACIÓN INTELIGENTE ---
    ubicacion = datos_ia.get("ENCABEZADO_ESTA_EN", "arriba").lower()
    if "abajo" in ubicacion:
        imagen_pil = imagen_pil.rotate(180, expand=True)
    elif "izquierda" in ubicacion:
        imagen_pil = imagen_pil.rotate(-90, expand=True)
    elif "derecha" in ubicacion:
        imagen_pil = imagen_pil.rotate(90, expand=True)

    # Recorte del borde inferior
    ancho, alto = imagen_pil.size
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
    
    # Extraer y cruzar datos
    cenco_base = datos_ia.get("CENCO", "No detectado")
    cliente_base = datos_ia.get("CLIENTE", "No detectado")
    nombre = datos_ia.get("NOMBRE", "No detectado")
    sucursal = datos_ia.get("SUCURSAL", "No detectado")
    
    cenco_final, empresa_final = cruzar_datos_inteligente(cenco_base, cliente_base, df_base)
    
    # Estampar
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
    
    return imagen_pil, nombre, cenco_final, cliente_base, sucursal, empresa_final

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
archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte masivo de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

if archivo_zimbra is not None:
    if st.button("🚀 Procesar Quincena y Generar Reportes", type="primary"):
        df_base = cargar_base_datos()
        temp_dir = tempfile.mkdtemp()
        
        with st.spinner("📦 Extrayendo archivos y conectando con Inteligencia Artificial..."):
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
            
            for i, archivo in enumerate(adjuntos):
                nombre_archivo = os.path.basename(archivo)
                status_text.text(f"La IA está leyendo y enderezando: {nombre_archivo}")
                
                rutas_est, datos_extraidos = procesar_documento(archivo, df_base, temp_dir)
                
                if not rutas_est:
                    progress_bar.progress((i + 1) / len(adjuntos))
                    continue
                
                todas_las_rutas_impresion.extend(rutas_est)
                
                for dato in datos_extraidos:
                    nombre, cenco, cliente, sucursal, empresa = dato
                    if cenco == "No detectado":
                        alertas += 1
                        
                    resultados_tabla.append({
                        "Documento": nombre_archivo,
                        "Colaborador": nombre,
                        "Cliente Extraído": cliente,
                        "Sucursal/Ciudad": sucursal,
                        "Cliente Oficial (BD)": empresa,
                        "CENCO Asignado": cenco
                    })
                
                progress_bar.progress((i + 1) / len(adjuntos))
                gc.collect()
            
            status_text.text("¡Procesamiento quincenal completado!")
            st.success(f"✅ Se procesaron y orientaron {len(todas_las_rutas_impresion)} hojas con éxito.")
            
            if alertas > 0:
                st.warning(f"⚠️ {alertas} planilla(s) quedaron con 'CENCO: No detectado'. Verifica el Excel.")
            
            if resultados_tabla:
                df_resultados = pd.DataFrame(resultados_tabla)
                st.dataframe(df_resultados, use_container_width=True)
                
                excel_buffer = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
                df_resultados.to_excel(excel_buffer.name, index=False)
                
                col1, col2 = st.columns(2)
                with col1:
                    with open(excel_buffer.name, "rb") as excel_file:
                        st.download_button("📊 Descargar Reporte Completo (Excel)", data=excel_file, file_name="Reporte_Quincenal_SERGEM.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                
                if todas_las_rutas_impresion:
                    ruta_pdf_final = os.path.join(temp_dir, "Planillas_Listas_Para_Imprimir.pdf")
                    with st.spinner("🖨️ Empaquetando PDF consolidado para impresión..."):
                        with Image.open(todas_las_rutas_impresion[0]) as primera_img:
                            primera_img_rgb = primera_img.convert('RGB')
                            if len(todas_las_rutas_impresion) > 1:
                                primera_img_rgb.save(ruta_pdf_final, save_all=True, append_images=generador_imagenes(todas_las_rutas_impresion[1:]))
                            else:
                                primera_img_rgb.save(ruta_pdf_final)
                    
                    with col2:
                        with open(ruta_pdf_final, "rb") as pdf_file:
                            st.download_button("🖨️ Descargar Todas las Planillas (Apaisadas)", data=pdf_file, file_name="Planillas_SERGEM_Quincena.pdf", mime="application/pdf", type="primary")
            
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()
