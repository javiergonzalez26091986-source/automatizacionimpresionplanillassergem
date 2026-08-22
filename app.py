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
import subprocess

# --- INSTALACIÓN DEL NAVEGADOR (SIN SUDO) ---
@st.cache_resource(show_spinner=False)
def instalar_navegador():
    # Instala solo el navegador Chromium, omitiendo las dependencias del SO
    # Esto evita el error de permisos al desplegar en la nube
    subprocess.run(["playwright", "install", "chromium"])

instalar_navegador()

# --- FUNCIONES DE SCRAPING Y EXTRACCIÓN ---
def descargar_exporte_zimbra(usuario, password, fecha_inicio, fecha_fin):
    ruta_descarga = "/tmp" if os.name != 'nt' else os.getcwd()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        try:
            # 1. Iniciar sesión
            page.goto("https://buzon.sergemsas.com/")
            # Reemplaza 'username' y 'password' por los names reales que saques con el script de JS
            page.fill("input[name='username']", usuario) 
            page.fill("input[name='password']", password) 
            # Reemplaza el selector del botón
            page.click("button[type='submit']") 
            page.wait_for_load_state("networkidle")
            
            # 2. Navegar a Importar/Exportar
            page.click("text=Preferencias")
            page.click("text=Importar/Exportar")
            time.sleep(2) 
            
            # 3. Configurar parámetros de exporte
            page.click("text=Configuración avanzada")
            
            # Ejemplo para desmarcar checkboxes (busca los IDs reales con el script de JS)
            # page.uncheck("#id_del_checkbox_contactos")
            # page.uncheck("#id_del_checkbox_agenda")
            
            str_inicio = fecha_inicio.strftime("%d/%m/%Y")
            str_fin = fecha_fin.strftime("%d/%m/%Y")
            # Reemplaza los selectores de las fechas
            page.fill("input[name='start_date']", str_inicio) 
            page.fill("input[name='end_date']", str_fin) 
            
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
    reader = easyocr.Reader(['es'])
    resultados = reader.readtext(ruta_imagen)
    for (bbox, texto, prob) in resultados:
        if "CENCO" in texto.upper():
            return texto 
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
            
            with st.spinner("Iniciando fase de preparación para OCR..."):
                st.info("Estructura base lista. Siguiente paso: procesar imágenes.")
