import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import gc
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

# --- PROTECCIÓN PARA IMÁGENES MASIVAS ---
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
    st.markdown("Procesamiento quincenal masivo con IA, cruce inteligente de datos y auto-orientación.")

# --- CARGA DE DATOS ---
@st.cache_data
def cargar_base_datos(ruta_archivo="centrosDeCostos_2.xlsx"):
    # Fallback por si el archivo 2 no existe
    if not os.path.exists(ruta_archivo):
        ruta_archivo = "centrosDeCostos.xlsx"
        
    try:
        df = pd.read_excel(ruta_archivo)
        df.columns = df.columns.str.upper().str.strip()
        
        # Mapeo dinámico de columnas por si cambian de nombre en el Excel
        col_cenco = next((col for col in df.columns if 'CENCO' in col or 'COSTO' in col), 'CENTRO_COSTO')
        col_empresa = next((col for col in df.columns if 'EMPRESA' in col or 'CLIENTE' in col), 'EMPRESA')
        col_ciudad = next((col for col in df.columns if 'CIUDAD' in col or 'SUCURSAL' in col), 'CIUDAD')
        col_nombre = next((col for col in df.columns if 'NOMBRE' in col or 'COLAB' in col or 'CONDUCTOR' in col), 'NOMBRE')

        for col in [col_cenco, col_empresa, col_ciudad, col_nombre]:
            if col not in df.columns:
                df[col] = "No detectado"
        
        df = df.rename(columns={col_cenco: 'CENCO_BD', col_empresa: 'CLIENTE_BD', col_ciudad: 'CIUDAD_BD', col_nombre: 'NOMBRE_BD'})
        
        for col in ['CENCO_BD', 'CLIENTE_BD', 'CIUDAD_BD', 'NOMBRE_BD']:
            df[col] = df[col].astype(str).str.strip().str.upper()
            
        return df
    except Exception as e:
        return pd.DataFrame(columns=['CENCO_BD', 'CLIENTE_BD', 'CIUDAD_BD', 'NOMBRE_BD'])

# --- FUNCIONES DE IA Y CRUCE INTELIGENTE ---
def analizar_con_gemini(imagen_pil):
    img_api = imagen_pil.copy()
    img_api.thumbnail((1024, 1024), Image.Resampling.LANCZOS) 
    
    prompt = """
    Eres un asistente experto en lectura de documentos. Analiza esta planilla de 'Registro de Prestación de Servicios'.
    Devuelve estrictamente un objeto JSON con estas claves:
    {
        "NOMBRE": "Nombre completo del colaborador o conductor. Si no hay, pon 'No detectado'",
        "CLIENTE": "Nombre de la empresa cliente (Ej: Covacrans, Exito, Olimpica, Surtimax, etc). Si no hay, pon 'No detectado'",
        "SUCURSAL": "Ciudad o nombre de la sucursal. Si no hay, pon 'No detectado'",
        "CENCO": "Número del centro de costo si está escrito. Si está vacío, pon 'No detectado'",
        "ORIENTACION_ACTUAL": "Responde solo con el número 0 si el texto está derecho, 90 si el texto está girado a la derecha, 180 si está de cabeza, o 270 si está girado a la izquierda."
    }
    """
    
    for intento in range(4): 
        try:
            # Frenado para evitar bloqueo de Google (15 requests/minuto)
            time.sleep(4.1) 
            response = model.generate_content([prompt, img_api])
            texto_respuesta = response.text
            
            if "```json" in texto_respuesta:
                texto_respuesta = texto_respuesta.split("```json")[1].split("```")[0]
            elif "```" in texto_respuesta:
                texto_respuesta = texto_respuesta.split("```")[1].split("```")[0]
                
            return json.loads(texto_respuesta.strip())
        except Exception:
            time.sleep(5)
            
    return {"NOMBRE": "No detectado", "CLIENTE": "No detectado", "SUCURSAL": "No detectado", "CENCO": "No detectado", "ORIENTACION_ACTUAL": 0}

def cruzar_datos_inteligente(cenco_ocr, cliente_ocr, sucursal_ocr, nombre_ocr, df_base):
    cenco_final = "No detectado"
    empresa_asignada = "No asignada (Validar)"
    
    cenco_cl = str(cenco_ocr).upper().strip()
    cliente_cl = str(cliente_ocr).upper().strip()
    sucursal_cl = str(sucursal_ocr).upper().strip()
    nombre_cl = str(nombre_ocr).upper().strip()
    
    if df_base.empty:
        return cenco_cl if cenco_cl not in ["NO DETECTADO", "NULL", "NONE", "VACÍO", ""] else "No detectado", "BD no cargada"

    # 1. Búsqueda por CENCO explícito
    if cenco_cl and cenco_cl not in ["NO DETECTADO", "NULL", "NONE", "VACÍO", ""]:
        coincidencia = df_base[df_base['CENCO_BD'] == cenco_cl]
        if not coincidencia.empty:
            return cenco_cl, coincidencia.iloc[0]['CLIENTE_BD']

    # 2. Deducción de Ciudad usando el Nombre (Similitud del 60%)
    if nombre_cl != "NO DETECTADO":
        nombres_bd = df_base['NOMBRE_BD'].unique().tolist()
        match_nombre = difflib.get_close_matches(nombre_cl, nombres_bd, n=1, cutoff=0.6)
        if match_nombre:
            ciudad_deducida = df_base[df_base['NOMBRE_BD'] == match_nombre[0]].iloc[0]['CIUDAD_BD']
            if ciudad_deducida and ciudad_deducida != "NAN":
                sucursal_cl = ciudad_deducida 

    # 3. Cruce por Cliente + Ciudad (Similitud del 45% para atrapar variaciones)
    if cliente_cl != "NO DETECTADO":
        clientes_bd = df_base['CLIENTE_BD'].unique().tolist()
        match_cliente = difflib.get_close_matches(cliente_cl, clientes_bd, n=1, cutoff=0.45)
        
        if match_cliente:
            cliente_oficial = match_cliente[0]
            subset_cliente = df_base[df_base['CLIENTE_BD'] == cliente_oficial]
            
            if sucursal_cl != "NO DETECTADO" and not subset_cliente.empty:
                ciudades_cliente = subset_cliente['CIUDAD_BD'].unique().tolist()
                match_ciudad = difflib.get_close_matches(sucursal_cl, ciudades_cliente, n=1, cutoff=0.5)
                
                if match_ciudad:
                    fila_match = subset_cliente[subset_cliente['CIUDAD_BD'] == match_ciudad[0]].iloc[0]
                    cenco_result = str(fila_match['CENCO_BD'])
                    return cenco_result if cenco_result != "NAN" else "No detectado", f"{cliente_oficial} (Deducido)"
            
            cenco_result = str(subset_cliente.iloc[0]['CENCO_BD'])
            return cenco_result if cenco_result != "NAN" else "No detectado", f"{cliente_oficial} (Sin ciudad)"
            
    return "No detectado", "No asignada (Validar)"

def optimizar_y_leer(imagen_pil, df_base):
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    datos_ia = analizar_con_gemini(imagen_pil)
    
    # --- ORIENTACIÓN VISUAL EXACTA ---
    try:
        orientacion = int(datos_ia.get("ORIENTACION_ACTUAL", 0))
    except:
        orientacion = 0
        
    if orientacion == 90:
        imagen_pil = imagen_pil.rotate(90, expand=True) # Gira anti-horario para corregir
    elif orientacion == 180:
        imagen_pil = imagen_pil.rotate(180, expand=True)
    elif orientacion == 270:
        imagen_pil = imagen_pil.rotate(-90, expand=True)

    # Forzar formato horizontal si continúa vertical
    ancho, alto = imagen_pil.size
    if alto > ancho:
        imagen_pil = imagen_pil.rotate(90, expand=True)
        ancho, alto = imagen_pil.size

    # Recorte del borde inferior (Marcas de agua)
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
    
    # Extracción de variables
    cenco_base = datos_ia.get("CENCO", "No detectado")
    cliente_base = datos_ia.get("CLIENTE", "No detectado")
    nombre_base = datos_ia.get("NOMBRE", "No detectado")
    sucursal_base = datos_ia.get("SUCURSAL", "No detectado")
    
    cenco_final, empresa_final = cruzar_datos_inteligente(cenco_base, cliente_base, sucursal_base, nombre_base, df_base)
    
    # --- ESTAMPADO DEL CENCO ---
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
    
    return imagen_pil, nombre_base, cenco_final, cliente_base, sucursal_base, empresa_final

# --- EXTRACCIÓN MASIVA ---
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

# --- FLUJO PRINCIPAL ---
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
            planillas_reales = 0 
            
            for i, archivo in enumerate(adjuntos):
                nombre_archivo = os.path.basename(archivo)
                status_text.text(f"La IA está leyendo y enderezando: {nombre_archivo}")
                
                rutas_est, datos_extraidos = procesar_documento(archivo, df_base, temp_dir)
                
                if not rutas_est:
                    progress_bar.progress((i + 1) / len(adjuntos))
                    continue
                
                for dato in datos_extraidos:
                    planillas_reales += 1
                    todas_las_rutas_impresion.append(rutas_est[datos_extraidos.index(dato)])
                    
                    nombre, cenco, cliente, sucursal, empresa = dato
                    if cenco == "No detectado":
                        alertas += 1
                        
                    resultados_tabla.append({
                        "Documento": nombre_archivo,
                        "Colaborador": nombre,
                        "CENCO Extraído": cenco,
                        "Cliente en Planilla": cliente,
                        "Ciudad/Sucursal": sucursal,
                        "Cliente Oficial (BD)": empresa
                    })
                
                progress_bar.progress((i + 1) / len(adjuntos))
                gc.collect()
            
            status_text.text("¡Procesamiento masivo finalizado!")
            st.success(f"✅ Se procesaron {planillas_reales} planillas con IA.")
            
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
                    with st.spinner("🖨️ Empaquetando PDF consolidado..."):
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
