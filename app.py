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

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide", page_icon="🖨️")
st.title("🖨️ Automatización de Planillas SERGEM")
st.markdown("Sistema integral con auto-orientación inteligente, estampado de CENCO y filtro de logos.")

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

# --- FUNCIONES AUXILIARES Y DE ORIENTACIÓN ---
def estampar_cenco_en_imagen(imagen_pil, cenco_texto):
    if not cenco_texto:
        cenco_texto = "CENCO NO DETECTADO"
        
    dibujo = ImageDraw.Draw(imagen_pil)
    try:
        tamano_fuente = int(imagen_pil.size[1] * 0.03) 
        fuente = ImageFont.truetype("arial.ttf", tamano_fuente)
    except IOError:
        fuente = ImageFont.load_default()
        
    texto = f" CENCO DETECTADO: {cenco_texto} "
    
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

def extraer_cenco_y_bbox(resultados):
    """Extrae el número de CENCO y las coordenadas espaciales de la palabra para saber dónde está el encabezado."""
    for (bbox, texto, prob) in resultados:
        if "CENC" in texto.upper():
            match = re.search(r'CENC[O0]?\s*[:\-\.]?\s*([A-Z0-9]+)', texto.upper())
            if match:
                return match.group(1), bbox
                
    texto_completo = " ".join([texto for (bbox, texto, prob) in resultados]).upper()
    match = re.search(r'CENC[O0]?\s*[:\-\.]?\s*([A-Z0-9]+)', texto_completo)
    if match:
        for (bbox, texto, prob) in resultados:
            if "CENC" in texto.upper():
                return match.group(1), bbox
        return match.group(1), None
        
    return None, None

def optimizar_y_leer_cenco(imagen_pil, reader):
    """Aplica Inteligencia Espacial para rotar la imagen correctamente y extraer el texto."""
    # 1. Corregir rotación básica de celulares (EXIF)
    imagen_pil = ImageOps.exif_transpose(imagen_pil)
    
    # 2. Forzar formato apaisado (horizontal)
    ancho, alto = imagen_pil.size
    if alto > ancho:
        imagen_pil = imagen_pil.rotate(90, expand=True)
        ancho, alto = imagen_pil.size

    # 3. Recortar marca de agua inferior
    recorte_inferior = int(alto * 0.03)
    imagen_pil = imagen_pil.crop((0, 0, ancho, alto - recorte_inferior))
    ancho, alto = imagen_pil.size

    # 4. Leer texto en memoria RAM
    img_np = np.array(imagen_pil)
    resultados = reader.readtext(img_np)
    cenco, bbox = extraer_cenco_y_bbox(resultados)
    
    if cenco and bbox is not None:
        # Calcular el centro Y de la palabra CENCO. Si está abajo de la mitad, la hoja está al revés.
        y_center = sum([p[1] for p in bbox]) / 4
        if y_center > alto / 2:
            imagen_pil = imagen_pil.rotate(180, expand=True)
    elif not cenco:
        # Si no se encontró, puede estar totalmente al revés impidiendo la lectura. Giramos e intentamos de nuevo.
        imagen_pil_180 = imagen_pil.rotate(180, expand=True)
        img_np_180 = np.array(imagen_pil_180)
        resultados_180 = reader.readtext(img_np_180)
        cenco_180, bbox_180 = extraer_cenco_y_bbox(resultados_180)
        
        if cenco_180:
            imagen_pil = imagen_pil_180
            cenco = cenco_180
            
    return cenco, imagen_pil

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
            
            # Archivos sueltos
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
                
            # ZIPs anidados de WhatsApp u otros
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

def analizar_y_estandarizar(ruta_archivo, reader):
    imagenes_optimizadas = []
    cencos_detectados = []
    
    try:
        if ruta_archivo.lower().endswith('.pdf'):
            paginas = convert_from_path(ruta_archivo, dpi=150)
            for pagina in paginas:
                pagina_rgb = pagina.convert('RGB')
                ancho, alto = pagina_rgb.size
                if ancho < 500 or alto < 500: # Filtro de logos
                    continue
                    
                cenco, img_optimizada = optimizar_y_leer_cenco(pagina_rgb, reader)
                if cenco: cencos_detectados.append(cenco)
                imagenes_optimizadas.append(img_optimizada)
        else:
            img = Image.open(ruta_archivo).convert('RGB')
            ancho, alto = img.size
            if ancho < 500 or alto < 500: # Filtro de logos
                return None, []
                
            cenco, img_optimizada = optimizar_y_leer_cenco(img, reader)
            if cenco: cencos_detectados.append(cenco)
            imagenes_optimizadas.append(img_optimizada)
            
        if not imagenes_optimizadas:
            return None, []
            
        cenco_final = cencos_detectados[0] if cencos_detectados else None
        
        imagenes_finales = []
        for img_opt in imagenes_optimizadas:
            img_estampada = estampar_cenco_en_imagen(img_opt, cenco_final)
            imagenes_finales.append(img_estampada)
            
        return cenco_final, imagenes_finales
    except Exception as e:
        return None, []

# --- INTERFAZ PRINCIPAL ---
df_base = cargar_base_datos()
reader = cargar_modelo_ocr()

archivo_zimbra = st.file_uploader("📂 Arrastra aquí el exporte de Zimbra (.tgz o .zip)", type=['tgz', 'zip', 'tar.gz'])

if archivo_zimbra is not None:
    if st.button("🚀 Procesar Archivo y Generar Bloque de Impresión", type="primary"):
        temp_dir = tempfile.mkdtemp()
        
        with st.spinner("📦 Descomprimiendo, analizando orientación y estandarizando planillas..."):
            adjuntos = procesar_archivo_comprimido(archivo_zimbra, temp_dir)
            
        if not adjuntos:
            st.warning("No se encontraron planillas adjuntas en los correos de este archivo.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados_tabla = []
            todas_las_imagenes_impresion = []
            alertas = 0
            planillas_reales = 0 
            
            for i, archivo in enumerate(adjuntos):
                nombre_archivo = os.path.basename(archivo)
                status_text.text(f"Orientando e inspeccionando: {nombre_archivo}")
                
                cenco_detectado, imagenes_estandarizadas = analizar_y_estandarizar(archivo, reader)
                
                if not imagenes_estandarizadas:
                    progress_bar.progress((i + 1) / len(adjuntos))
                    continue
                
                planillas_reales += 1
                todas_las_imagenes_impresion.extend(imagenes_estandarizadas)
                
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
            
            status_text.text("¡Procesamiento y optimización completados!")
            st.success(f"✅ Se analizaron y orientaron {planillas_reales} planillas horizontalmente con éxito.")
            
            if alertas > 0:
                st.error(f"⚠️ Atención Doña Yesenia: Hay {alertas} planilla(s) sin un número CENCO detectado.")
            
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
                            file_name="Reporte_CENCOS_Cruzados.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                
                if todas_las_imagenes_impresion:
                    ruta_pdf_final = os.path.join(temp_dir, "Planillas_Listas_Para_Imprimir.pdf")
                    todas_las_imagenes_impresion[0].save(
                        ruta_pdf_final, 
                        save_all=True, 
                        append_images=todas_las_imagenes_impresion[1:]
                    )
                    
                    with col2:
                        with open(ruta_pdf_final, "rb") as pdf_file:
                            st.download_button(
                                label="🖨️ Descargar Todas las Planillas Orientadas",
                                data=pdf_file,
                                file_name="Planillas_SERGEM_Optimizadas.pdf",
                                mime="application/pdf",
                                type="primary"
                            )
            
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()
