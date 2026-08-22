import streamlit as st
import os
import tarfile
import email
from email import policy
import easyocr
import cv2
import numpy as np
from playwright.sync_api import sync_playwright
import time

# --- CONFIGURACIÓN INICIAL Y DESCARGA DE NAVEGADORES ---
@st.cache_resource
def instalar_playwright():
    os.system("playwright install chromium")
    os.system("playwright install-deps chromium")

instalar_playwright()

# --- FUNCIONES DE SCRAPING Y EXTRACCIÓN ---
def descargar_exporte_zimbra(usuario, password, fecha_inicio, fecha_fin):
    ruta_descarga = "/tmp" if os.name != 'nt' else os.getcwd()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Configurar el contexto para aceptar descargas
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        try:
            # 1. Iniciar sesión
            page.goto("https://buzon.sergemsas.com/")
            page.fill("input[name='username']", usuario) # ### IMPORTANTE: Verificar selector
            page.fill("input[name='password']", password) # ### IMPORTANTE: Verificar selector
            page.click("button[type='submit']") # ### IMPORTANTE: Verificar selector
            page.wait_for_load_state("networkidle")
            
            # 2. Navegar a Importar/Exportar
            page.click("text=Preferencias")
            page.click("text=Importar/Exportar")
            time.sleep(2) # Esperar a que cargue el módulo
            
            # 3. Configurar parámetros de exporte
            page.click("text=Configuración avanzada")
            
            # Desmarcar opciones no deseadas (Contactos, Agenda, Tareas, Maletín)
            # ### IMPORTANTE: Necesitarás buscar los IDs exactos de los checkboxes en Zimbra
            # Ejemplo: page.uncheck("#z_contactos_checkbox")
            
            # Llenar fechas (el formato de Zimbra suele ser DD/MM/AAAA)
            str_inicio = fecha_inicio.strftime("%d/%m/%Y")
            str_fin = fecha_fin.strftime("%d/%m/%Y")
            page.fill("input[name='start_date']", str_inicio) # ### IMPORTANTE: Verificar selector
            page.fill("input[name='end_date']", str_fin) # ### IMPORTANTE: Verificar selector
            
            # 4. Descargar
            with page.expect_download() as download_info:
                page.click("button:has-text('Exportar')")
            
            download = download_info.value
            archivo_final = os.path.join(ruta_descarga, download.suggested_filename)
            download.save_as(archivo_final)
            
            browser.close()
            return archivo_final
            
        except Exception as e:
            browser.close()
            st.error(f"Error en la automatización web: {e}")
            return None

def procesar_tgz(ruta_tgz):
    adjuntos_extraidos = []
    directorio_extraccion = "planillas_extraidas"
    os.makedirs(directorio_extraccion, exist_ok=True)
    
    with tarfile.open(ruta_tgz, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith(".eml"):
                f = tar.extractfile(member)
                if f is not None:
                    msg = email.message_from_bytes(f.read(), policy=policy.default)
                    for part in msg.walk():
                        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                            continue
                        
                        filename = part.get_filename()
                        if filename:
                            ruta_archivo = os.path.join(directorio_extraccion, filename)
                            with open(ruta_archivo, 'wb') as new_file:
                                new_file.write(part.get_payload(decode=True))
                            adjuntos_extraidos.append(ruta_archivo)
                            
    return adjuntos_extraidos

def leer_cenco_easyocr(ruta_imagen):
    # Inicializar EasyOCR (solo en español)
    reader = easyocr.Reader(['es'])
    
    # Aquí iría el preprocesamiento con cv2 si es necesario (escala de grises, threshold)
    
    resultados = reader.readtext(ruta_imagen)
    for (bbox, texto, prob) in resultados:
        if "CENCO" in texto.upper():
            return texto # Retorna el texto encontrado alrededor de CENCO
    return None

# --- INTERFAZ DE STREAMLIT ---
st.set_page_config(page_title="Gestor de Planillas SERGEM", layout="wide")
st.title("🖨️ Automatización de Planillas - Fase 1")

with st.sidebar:
    st.header("Credenciales Zimbra")
    usuario = st.text_input("Usuario", value="planillas")
    password = st.text_input("Contraseña", type="password")

col1, col2 = st.columns(2)
fecha_inicio = col1.date_input("Fecha de inicio")
fecha_fin = col2.date_input("Fecha de fin")

if st.button("Descargar y Procesar"):
    if not password:
        st.warning("Por favor, ingresa la contraseña en la barra lateral.")
    else:
        with st.spinner("Navegando en Zimbra y descargando correos... (Esto puede tardar unos minutos)"):
            ruta_tgz = descargar_exporte_zimbra(usuario, password, fecha_inicio, fecha_fin)
            
        if ruta_tgz:
            with st.spinner("Descomprimiendo archivos y extrayendo adjuntos..."):
                lista_archivos = procesar_tgz(ruta_tgz)
                st.success(f"Se extrajeron {len(lista_archivos)} adjuntos.")
            
            with st.spinner("Pasando archivos por OCR..."):
                # Aquí se iteraría sobre lista_archivos
                # cenco = leer_cenco_easyocr(lista_archivos[0])
                st.info("Fase de lectura y visualización lista para programar.")
