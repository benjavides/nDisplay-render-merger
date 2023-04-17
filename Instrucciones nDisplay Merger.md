# nDisplayMerger

Al exportar nDisplay usando Movie Render Queue en Unreal 5.1 o superior, la salida es una imagen por frame por viewport. Unreal no compone los viewports de la forma especificada en la sección "Output Mapping" del archivo de configuración de nDisplay.

Este programa recibe como input 

1. la carpeta con imágenes exportadas
2. archivo de configuración de nDisplay (que dice cómo componer los viewports)

y junta todos los viewports en una sola imagen por frame.



# Uso

1. Hacer el archivo de configuración de nDisplay. Importante que esté hecho el `STEP 3`.
   ![image-20230417145050771](./assets/image-20230417145050771.png)

2. Exportar el archivo de configuración
   ![image-20230417145134182](./assets/image-20230417145134182.png)

3. Renderizar usando nDisplay con el Movie Render Queue (Solo Unreal 5.1 en adelante)
   ![image-20230417145432511](./assets/image-20230417145432511.png)

   ![image-20230417145547068](./assets/image-20230417145547068.png)

4. Abrir el programa `nDisplayMerger.exe` y seleccionar el archivo de configuración de nDisplay exportado y la carpeta con las imágenes renderizadas. Click en `Run Compositor`
   ![image-20230417145652092](./assets/image-20230417145652092.png)

5. El resultado es una imagen compuesta por cada frame
   ![image-20230417150432899](./assets/image-20230417150432899.png)