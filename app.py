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

# --- PROTECCIÓN DE PIL PARA IMÁGENES PESADAS ---
Image.MAX_IMAGE_PIXELS = None 

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
    st.markdown("Sistema integral con auto-orientación espacial, filtro de firmas y escudo de memoria.")

# --- CARGA DE DATOS Y MODELOS ---
@st.cache_data
def cargar_base_datos(ruta_archivo="centrosDeCostos.xlsx"):
    try:
        df = pd.read_excel(ruta_archivo)
        df['CENTRO_COSTO'] = df['CENTRO_COSTO'].astype(str).str.strip()
        return df
    except Exception as e:
        return pd.DataFrame(columns=['EMPRESA', 'CENTRO_COSTO'])

@st.cache_resource
def cargar_modelo_ocr():
    return easyocr.Reader(['es'])

# --- FUNCIONES DE LECTURA Y ORIENTACIÓN INTELIGENTE ---
def extraer_numero_cenco(resultados):
    texto_completo = " ".join([texto for (bbox, texto, prob) in resultados]).upper()
    match = re.search(r'CENC[O0Q]?\s*[:\-\.]?\s*([A-Z0-9]{1,10})', texto_completo)
    
    if match:
        posible_cenco = match.group(1)
        falsos_positivos = ['OBS', 'PP', 'MES', 'ANO', 'AÑO', 'SUCURSAL', 'C', 'O', '0', 'ABP', 'ABR', 'ABE', 'FIRMA']
        if posible_cenco in falsos_positivos:
            return None
        return posible_cenco
    return None

def optimizar_y_leer_cenco(imagen_pil, reader):
    # 1. Corregir rotación interna (EXIF)
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # --- ESCUDO ANTI-CAÍDAS: Reducir imagen pesada a un máximo seguro ---
    # Esto salva la RAM del servidor al aplicar OCR
    imagen_pil.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    
    # 2. Forzar siempre a formato apaisado (horizontal)
    ancho, alto = imagen_pil.size
    if alto > ancho:
        imagen_pil = imagen_pil.rotate(90, expand=True)
        ancho, alto = imagen_pil.size

    # 3. Leer texto inicial para verificar dónde está el encabezado
    img_np = np.array(imagen_pil)
    resultados = reader.readtext(img_np)
    
    # 4. Inteligencia Espacial
    keywords = ["SERGEM", "FORMATO", "REGISTRO", "PRESTACION", "CLIENTE", "FECHA", "FIRMA", "CENCO", "SUCURSAL"]
    y_coords = []
    
    for (bbox, texto, prob) in resultados:
        if any(kw in texto.upper() for kw in keywords):
            y_center = sum(p[1] for p in bbox) / 4
            y_coords.append(y_center)
            
    necesita_rotar = False
    if y_coords:
        avg_y = sum(y_coords) / len(y_coords)
        if avg_y > alto / 2:
            necesita_rotar = True
    else:
        necesita_rotar = True 

    # 5. Aplicar rotación de 180 grados si es necesario
    if necesita_rotar:
        imagen_pil = imagen_pil.rotate(180, expand=True)
        del img_np 
        gc.collect()
        
        img_np = np.array(imagen_pil)
        resultados = reader.readtext(img_np)

    # Extraer el CENCO final validado
    cenco = extraer_numero_cenco(resultados)
    
    del img_np
    gc.collect()
    
    # Recorte del borde inferior
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
            
    return cenco, imagen_pil

def estampar_cenco_en_imagen(imagen_pil, cenco_texto):
    dibujo = ImageDraw.Draw(imagen_pil)
    try:
        tamano_fuente = int(imagen_pil.size[1] * 0.03) 
        fuente = ImageFont.truetype("arial.ttf", tamano_fuente)
    except IOError:
        fuente = ImageFont.load_default()
        
    if not cenco_texto:
        texto = " CENCO: No detectado "
    else:
        texto = f" CENCO: {cenco_texto} "
    
    try:
        caja_texto = dibujo.textbbox((0, 0), texto, font=fuente)
        ancho_texto = caja_texto[2] - caja_texto[0]
        alto_texto = caja_texto[3] - caja_texto[1]
    except AttributeError:
        ancho_texto, alto_texto = dibujo.textsize(texto, font=fuente)

    x = imagen_pil.size[0] - ancho_texto - 20
    y = 20

    dibujo.rectangle((x, y, x + ancho_texto, y + alto_texto + 10), fill="white", outline="black", width=2)
    dibujo.text((x, y + 5), texto, fill="red", font=fuente)
    
    return imagen_pil

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

def analizar_y_estandarizar(ruta_archivo, reader, temp_dir):
    rutas_imagenes_optimizadas = []
    cencos_detectados = []
    
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_archivo, dpi=120)
            for pagina in paginas:
                pagina_rgb = pagina.convert('RGB')
                ancho, alto = pagina_rgb.size
                if ancho < 500 or alto < 500: # Filtro de logos
                    continue
                    
                cenco, img_optimizada = optimizar_y_leer_cenco(pagina_rgb, reader)
                if cenco: cencos_detectados.append(cenco)
                
                img_final = estampar_cenco_en_imagen(img_optimizada, cenco)
                
                ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
                img_final.save(ruta_temp, 'JPEG', quality=85)
                rutas_imagenes_optimizadas.append(ruta_temp)
                
                del pagina_rgb, img_optimizada, img_final
                gc.collect()
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            ancho, alto = img.size
            if ancho < 500 or alto < 500: # Filtro de logos
                return None, []
                
            cenco, img_optimizada = optimizar_y_leer_cenco(img, reader)
            if cenco: cencos_detectados.append(cenco)
            
            cenco_final = cencos_detectados[0] if cencos_detectados else None
            img_final = estampar_cenco_en_imagen(img_optimizada, cenco_final)
            
            ruta_temp = os.path.join(temp_dir, f"proc_{os.urandom(4).hex()}.jpg")
            img_final.save(ruta_temp, 'JPEG', quality=85)
            rutas_imagenes_optimizadas.append(ruta_temp)
            
            del img, img_optimizada, img_final
            gc.collect()
            
        cenco_final = cencos_detectados[0] if cencos_detectados else None
        return cenco_final, rutas_imagenes_optimizadas
    except Exception as e:
        return None, []

def generador_imagenes(rutas):
    """Generador que carga las imágenes una por una para no saturar la RAM al armar el PDF"""
    for ruta in rutas:
        with Image.open(ruta) as img:
            yield img.convert('RGB')
        gc.collect()

# --- FLUJO PRINCIPAL DE INTERFAZ ---
archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

if archivo_zimbra is not None:
    if st.button("🚀 Procesar Archivo y Generar Bloque de Impresión", type="primary"):
        
        df_base = cargar_base_datos()
        reader = cargar_modelo_ocr()
        
        temp_dir = tempfile.mkdtemp()
        
        with st.spinner("📦 Descomprimiendo, calculando orientación espacial y protegiendo memoria..."):
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
                status_text.text(f"Mapeando coordenadas y analizando: {nombre_archivo}")
                
                cenco_detectado, rutas_estandarizadas = analizar_y_estandarizar(archivo, reader, temp_dir)
                
                if not rutas_estandarizadas:
                    progress_bar.progress((i + 1) / len(adjuntos))
                    continue
                
                planillas_reales += 1
                todas_las_rutas_impresion.extend(rutas_estandarizadas)
                
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
                    "CENCO Extraído": cenco_detectado if cenco_detectado else "No detectado",
                    "Cliente Asignado": empresa_asignada
                })
                
                progress_bar.progress((i + 1) / len(adjuntos))
                gc.collect()
            
            status_text.text("¡Procesamiento finalizado de manera estable!")
            st.success(f"✅ Se analizaron y orientaron {planillas_reales} planillas horizontalmente con éxito.")
            
            if alertas > 0:
                st.error(f"⚠️ Atención: Hay {alertas} planilla(s) sin un número CENCO legible.")
            
            if resultados_tabla:
                df_resultados = pd.DataFrame(resultados_tabla)
                st.dataframe(df_resultados, use_container_width=True)
                
                excel_buffer = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
                df_resultados.to_excel(excel_buffer.name, index=False)
                
                col1, col2 = st.columns(2)
                
                with col1:
                    with open(excel_buffer.name, "rb") as excel_file:
                        st.download_button(
                            label="📊 Descargar Reporte en Excel",
                            data=excel_file,
                            file_name="Reporte_CENCOS_SERGEM.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                
                if todas_las_rutas_impresion:
                    ruta_pdf_final = os.path.join(temp_dir, "Planillas_Listas_Para_Imprimir.pdf")
                    with st.spinner("🖨️ Empaquetando y organizando PDF final..."):
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
                                label="🖨️ Descargar Todas las Planillas",
                                data=pdf_file,
                                file_name="Planillas_SERGEM_Alineadas.pdf",
                                mime="application/pdf",
                                type="primary"
                            )
            
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()
