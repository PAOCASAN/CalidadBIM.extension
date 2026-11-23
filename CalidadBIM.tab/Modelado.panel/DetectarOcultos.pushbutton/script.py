# -*- coding: utf-8 -*-
import csv
import codecs
from System.Collections.Generic import List
from pyrevit import revit, DB, forms, script

doc = revit.doc

# 1. Obtener vistas reales del proyecto
vistas_validas = []
mapa_vistas = {}

for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
    if v.IsTemplate:
        continue
    if v.ViewType in [
        DB.ViewType.FloorPlan,
        DB.ViewType.CeilingPlan,
        DB.ViewType.EngineeringPlan,
        DB.ViewType.AreaPlan,
        DB.ViewType.Section,
        DB.ViewType.Elevation,
        DB.ViewType.ThreeD,
        DB.ViewType.Detail
    ]:
        escala = "1:{}".format(v.Scale) if v.Scale else "S/E"
        etiqueta = "[{}] {} ({})".format(escala, v.Name, v.ViewType)
        vistas_validas.append(etiqueta)
        mapa_vistas[etiqueta] = v

vistas_validas.sort()

# 2. Ventana flotante 1: Elegir las vistas a auditar
vistas_sel_labels = forms.SelectFromList.show(
    vistas_validas,
    title="Paso 1: Seleccionar Vistas a Auditar",
    button_name="Escanear Vistas",
    multiselect=True,
    width=650,
    height=500
)

if not vistas_sel_labels:
    script.exit()

vistas_a_revisar = [mapa_vistas[lbl] for lbl in vistas_sel_labels]

# 3. Categorias a auditar
target_cats = [
    DB.BuiltInCategory.OST_MedicalEquipment,
    DB.BuiltInCategory.OST_Furniture,
    DB.BuiltInCategory.OST_GenericModel,
    DB.BuiltInCategory.OST_IOSModelGroups
]

elementos_modelo = []
for bic in target_cats:
    elems = (
        DB.FilteredElementCollector(doc)
        .OfCategory(bic)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    elementos_modelo.extend(elems)

# 4. Detectar ocultos en las vistas seleccionadas
resultados = []
opciones_tabla = []
mapa_resultados = {}

for vista in vistas_a_revisar:
    escala_vista = "1:{}".format(vista.Scale) if vista.Scale else "S/E"
    for el in elementos_modelo:
        try:
            if el.IsHidden(vista):
                cat_name = el.Category.Name if el.Category else "Sin categoría"
                fam_name = ""
                tipo_name = el.Name if hasattr(el, "Name") else ""
                el_type = doc.GetElement(el.GetTypeId())
                if el_type:
                    fam_param = el_type.get_Parameter(DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                    if fam_param:
                        fam_name = fam_param.AsString() or ""

                codigo_eq = "-"
                for p_name in ["CODIGO", "Código", "Marca", "Mark"]:
                    p = el.LookupParameter(p_name)
                    if p and p.HasValue:
                        val = p.AsString() or p.AsValueString()
                        if val:
                            codigo_eq = val
                            break

                id_str = str(el.Id)
                item_data = {
                    "id_obj": el.Id,
                    "id": id_str,
                    "vista_obj": vista,
                    "vista": vista.Name,
                    "escala": escala_vista,
                    "categoria": cat_name,
                    "codigo": codigo_eq,
                    "familia": fam_name,
                    "tipo": tipo_name
                }
                resultados.append(item_data)

                # Fila estructurada tipo tabla para la ventana flotante
                fila_texto = "ID: {}  |  {}  |  {}  |  {}  |  Cód: {}  |  {} : {}".format(
                    id_str, escala_vista, vista.Name, cat_name, codigo_eq, fam_name, tipo_name
                )
                opciones_tabla.append(fila_texto)
                mapa_resultados[fila_texto] = item_data
        except Exception:
            pass

if not resultados:
    forms.alert(
        "¡Todo limpio! No se encontraron elementos ocultos en las {} vistas seleccionadas.".format(len(vistas_a_revisar)),
        title="Auditoría de Ocultos - Sin hallazgos"
    )
    script.exit()

# 5. Exportar primero a Excel (CSV) si el usuario lo desea
ruta_csv = forms.save_file(
    file_ext="csv",
    default_name="Reporte_Elementos_Ocultos.csv",
    title="Guardar Reporte en Excel (CSV) - Opcional (Cancelar para ir directo a la tabla)"
)

if ruta_csv:
    with codecs.open(ruta_csv, "w", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["ID", "Escala", "Vista", "Categoria", "Codigo_Marca", "Familia", "Tipo"])
        for r in resultados:
            writer.writerow([
                r["id"],
                r["escala"],
                r["vista"],
                r["categoria"],
                r["codigo"],
                r["familia"],
                r["tipo"]
            ])

# 6. Ventana flotante 2: Tabla interactiva para quitar lo oculto (Unhide)
seleccionados = forms.SelectFromList.show(
    opciones_tabla,
    title="Paso 2: {} Ocultos Detectados — Marca los que deseas MOSTRAR en sus vistas".format(len(resultados)),
    button_name="Quitar Oculto a Seleccionados",
    multiselect=True,
    width=950,
    height=550
)

if seleccionados:
    # Agrupar IDs por vista para ejecutar UnhideElements de forma limpia
    por_vista = {}
    for fila in seleccionados:
        dato = mapa_resultados[fila]
        v_obj = dato["vista_obj"]
        if v_obj.Id not in por_vista:
            por_vista[v_obj.Id] = {"vista": v_obj, "ids": []}
        por_vista[v_obj.Id]["ids"].append(dato["id_obj"])

    with revit.Transaction("Mostrar elementos ocultos (Auditoría BIM)"):
        for v_id, paquete in por_vista.items():
            vista_target = paquete["vista"]
            lista_ids = List[DB.ElementId](paquete["ids"])
            vista_target.UnhideElements(lista_ids)

    forms.alert(
        "Se quitó el estado oculto a {} elemento(s) correctamente.".format(len(seleccionados)),
        title="Elementos Restaurados"
    )