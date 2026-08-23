import streamlit as st
import os
import tarfile
import zipfile
import email
from email import policy
import gc
from PIL import Image
from pdf2image import convert_from_path
import tempfile
import shutil
import pandas as pd
import io
import google.generativeai as genai
import json

# --- PROTECCIÓN DE PIL PARA IMÁGENES PESADAS ---
Image.MAX_IMAGE_PIXELS = None 

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="sergemLogo.ico")

# --- CONFIGURACIÓN DE GEMINI ---
# Obtiene la API Key de los Secrets de Streamlit
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
except Exception:
    st.error("⚠️ Falta configurar la GEMINI_API_KEY en los Secrets de Streamlit.")

# Configuramos el modelo para que responda siempre en formato JSON
generation_config = {
  "temperature": 0.1,
  "response_mime_type": "application/json",
}
model = genai.GenerativeModel("gemini-1.5-flash", generation_config=generation_config)


# --- ENCABEZADO CON LOGO ---
col1, col2 = st.columns([1, 4])
with col1:
    if os.path.exists("sergemLogo.png"):
        st.image("sergemLogo.png", width=180)
    else:
        st.write("🏢 SERGEM")
with col2:
    st.title("Automatización de Planillas SERGEM")
    st.markdown("Sistema inteligente con procesamiento en la nube, extracción estructurada y blindaje de servidor.")

# --- CARGA DE DATOS ---
@st.cache_data
def cargar_base_datos(ruta_archivo="centrosDeCostos.xlsx"):
    try:
        df = pd.read_excel(ruta_archivo)
        df['CENTRO_COSTO'] = df['CENTRO_COSTO'].astype(str).str.strip()
        # Limpiamos también la empresa para facilitar búsquedas
        df['EMPRESA_CLEAN'] = df['EMPRESA'].astype(str).str.upper().str.strip()
        return df
    except Exception as e:
        return pd.DataFrame(columns=['EMPRESA', 'CENTRO_COSTO', 'EMPRESA_CLEAN'])

# --- FUNCIONES DE EXTRACCIÓN CON IA (GEMINI) ---
def extraer_datos_con_gemini(imagen_pil):
    """
    Envía la imagen a Gemini para que la enderece mentalmente y extraiga los datos clave.
    """
    # 1. Reducimos el tamaño para acelerar la subida a internet
    img_opt = imagen_pil.copy()
    img_opt.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    
    prompt = """
    Analiza esta planilla de 'Registro de Prestación de Servicios'. La imagen puede estar rotada o al revés, enderézala mentalmente para leerla.
    Extrae la siguiente información y devuélvela estrictamente en este formato JSON:
    {
        "NOMBRE_CONDUCTOR": "Nombre del conductor o colaborador",
        "CLIENTE": "Nombre del cliente (ej. Exico Aquarela, Corpulla San, Covacrans, etc)",
        "SUCURSAL": "Ciudad o sucursal escrita",
        "CENCO": "Número del centro de costos. Si está vacío o ilegible, devuelve null"
    }
    """
    try:
        response = model.generate_content([prompt, img_opt])
        datos = json.loads(response.text)
        
        # Validaciones de nulidad
        cenco = datos.get("CENCO")
        if not cenco or str(cenco).strip() == "" or str(cenco).upper() in ["NULL", "NO DETECTADO", "VACÍO", "NONE"]:
            cenco = None
            
        nombre = datos.get("NOMBRE_CONDUCTOR", "No detectado")
        cliente = datos.get("CLIENTE", "No detectado")
        sucursal = datos.get("SUCURSAL", "No detectado")
        
        return cenco, nombre, cliente, sucursal
    except Exception as e:
        return None, "Error de lectura", "Error de lectura", "Error de lectura"


def optimizar_y_leer_cenco(imagen_pil):
    """Aplica IA para extraer texto y fuerza la imagen a formato apaisado para impresión"""
    # 1. Extraer datos usando la IA antes de manipular la imagen original
    cenco, nombre, cliente, sucursal = extraer_datos_con_gemini(imagen_pil)
    
    # 2. Forzar siempre a formato apaisado (horizontal) para el PDF de impresión
    ancho, alto = imagen_pil.size
    if alto > ancho:
        imagen_pil = imagen_pil.rotate(90, expand=True)
        ancho, alto = imagen_pil.size
        
    # Recorte del borde inferior (ej. marcas de CamScanner)
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
    
    return cenco, nombre, cliente, sucursal, imagen_pil


def cruzar_datos(cenco, cliente, sucursal, df_base):
    """Intenta cruzar por CENCO. Si no hay CENCO, intenta cruzar por similitud de Cliente/Sucursal."""
    empresa_asignada = "No asignada (Validar)"
    cenco_final = cenco
    
    if cenco:
        # Cruce directo por CENCO
        coincidencia = df_base[df_base['CENTRO_COSTO'] == str(cenco)]
        if not coincidencia.empty:
            empresa_asignada = coincidencia.iloc[0]['EMPRESA']
        else:
            empresa_asignada = "CENCO no registrado"
    else:
        # Si no hay CENCO, buscamos si el cliente mencionado existe en la base
        cliente_upper = str(cliente).upper().strip()
        if cliente_upper != "NO DETECTADO":
            # Búsqueda por similitud básica
            coincidencia_cliente = df_base[df_base['EMPRESA_CLEAN'].str.contains(cliente_upper, na=False)]
            if not coincidencia_cliente.empty:
                empresa_asignada = coincidencia_cliente.iloc[0]['EMPRESA']
                cenco_final = coincidencia_cliente.iloc[0]['CENTRO_COSTO']
                empresa_asignada += " (Deducido por Cliente)"
            else:
                empresa_asignada = "Cliente no encontrado en BD"
                
    return cenco_final, empresa_asignada

# --- EXTRACCIÓN MASIVA DE CORREOS ---
def extraer_adjuntos_de_eml(contenido_eml, directorio_salida):
    adjuntos = []
    extensiones_validas = ('.pdf', '.jpeg', '.jpg', '.png', '.bmp', '.webp', '.tiff')
    msg = email.message_from_bytes(contenido_eml, policy=policy.default)
    
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
                zip_bytes = part.get_payload(decode=True)
                try:
                    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_ref:
                        for zip_filename in zip_ref.namelist():
                            if zip_filename.lower().endswith(extensiones_validas):
                                file_content = zip_ref.read(zip_filename)
                                base_name = os.path.basename(zip_filename)
                                if not base_name: 
                                    continue
                                
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

def analizar_y_estandarizar(ruta_archivo, temp_dir):
    resultados_doc = []
    
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_archivo, dpi=120)
            for pagina in paginas:
                pagina_rgb = pagina.convert('RGB')
                ancho, alto = pagina_rgb.size
                if ancho < 500 or alto < 500: # Filtro de logos
                    continue
                    
                cenco, nombre, cliente, sucursal, img_optimizada = optimizar_y_leer_cenco(pagina_rgb)
                
                # Streaming de disco para no colapsar memoria
                ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
                img_optimizada.save(ruta_temp, 'JPEG', quality=85)
                
                resultados_doc.append({
                    "ruta": ruta_temp,
                    "cenco": cenco,
                    "nombre": nombre,
                    "cliente": cliente,
                    "sucursal": sucursal
                })
                del pagina_rgb, img_optimizada
                gc.collect()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            ancho, alto = img.size
            if ancho < 500 or alto < 500: # Filtro de logos
                return []
                
            cenco, nombre, cliente, sucursal, img_optimizada = optimizar_y_leer_cenco(img)
            
            # Streaming de disco
            ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
            img_optimizada.save(ruta_temp, 'JPEG', quality=85)
            
            resultados_doc.append({
                "ruta": ruta_temp,
                "cenco": cenco,
                "nombre": nombre,
                "cliente": cliente,
                "sucursal": sucursal
            })
            del img, img_optimizada
            gc.collect()
            
        return resultados_doc
    except Exception as e:
        return []

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
        
        with st.spinner("📦 Descomprimiendo archivos y protegiendo memoria..."):
            adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
            
        if not adjuntos:
            st.warning("No se encontraron planillas adjuntas en los correos de este archivo.")
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
                
                datos_extraidos = analizar_y_estandarizar(archivo, temp_dir)
                
                if not datos_extraidos:
                    progress_bar.progress((i + 1) / len(adjuntos))
                    continue
                
                for dato in datos_extraidos:
                    planillas_reales += 1
                    todas_las_rutas_impresion.append(dato['ruta'])
                    
                    # Lógica de Cruce Avanzado
                    cenco_final, empresa_asignada = cruzar_datos(dato['cenco'], dato['cliente'], dato['sucursal'], df_base)
                    
                    if not cenco_final:
                        alertas += 1
                    
                    resultados_tabla.append({
                        "Documento": nombre_archivo,
                        "Colaborador": dato['nombre'],
                        "CENCO Extraído": cenco_final if cenco_final else "No detectado",
                        "Cliente en Planilla": dato['cliente'],
                        "Ciudad/Sucursal": dato['sucursal'],
                        "Cliente Oficial (BD)": empresa_asignada
                    })
                
                progress_bar.progress((i + 1) / len(adjuntos))
                gc.collect()
            
            status_text.text("¡Procesamiento masivo finalizado!")
            st.success(f"✅ Se procesaron {planillas_reales} planillas con IA.")
            
            if alertas > 0:
                st.warning(f"⚠️ {alertas} planilla(s) no tienen CENCO y no se pudo deducir por el cliente.")
            
            if resultados_tabla:
                df_resultados = pd.DataFrame(resultados_tabla)
                st.dataframe(df_resultados, use_container_width=True)
                
                excel_buffer = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
                df_resultados.to_excel(excel_buffer.name, index=False)
                
                col1, col2 = st.columns(2)
                
                with col1:
                    with open(excel_buffer.name, "rb") as excel_file:
                        st.download_button(
                            label="📊 Descargar Reporte Completo (Excel)",
                            data=excel_file,
                            file_name="Reporte_Quincenal_SERGEM.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                
                if todas_las_rutas_impresion:
                    ruta_pdf_final = os.path.join(temp_dir, "Planillas_Listas_Para_Imprimir.pdf")
                    with st.spinner("🖨️ Empaquetando PDF consolidado..."):
                        with Image.open(todas_las_rutas_impresion[0]) as primera_img:
                            primera_img_rgb = primera_img.convert('RGB')
                            if len(todas_las_rutas_impresion) > 1:
                                primera_img_rgb.save(
                                    ruta_pdf_final, 
                                    save_all=True, 
                                    append_images=generador_imagenes(todas_las_rutas_impresion[1:])
                                )
                            else:
                                primera_img_rgb.save(ruta_pdf_final)
                    
                    with col2:
                        with open(ruta_pdf_final, "rb") as pdf_file:
                            st.download_button(
                                label="🖨️ Descargar Todas las Planillas (Apaisadas)",
                                data=pdf_file,
                                file_name="Planillas_SERGEM_Quincena.pdf",
                                mime="application/pdf",
                                type="primary"
                            )
            
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()
