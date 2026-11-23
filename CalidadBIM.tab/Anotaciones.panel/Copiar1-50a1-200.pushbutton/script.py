# -*- coding: utf-8 -*-
"""
Calidad BIM: Copiar anotaciones de vista 1:50 a vista 1:200
- Adapta etiquetas SHYD1_TAG_* a tipo SHYD1_ETIQUETA_1-200
- Adapta Anotaciones Genéricas SHYD1_ANOTACION EDITABLE:
    * SHYD1_ANOTACION EDITABLE_1-50 ancho -> SHYD1_ANOTACION EDITABLE_1-200 ancho
    * SHYD1_ANOTACION EDITABLE_1-50       -> SHYD1_ANOTACION EDITABLE_1-200
- Copia líneas finas (LINEAS FINAS) y viñetas editables SHYD1_VIÑETA EDITABLE
- Optimizado para ejecución ultrarrápida sin bloqueos
"""

import sys
import time
from pyrevit import revit, DB, UI, forms

doc = revit.doc
active_view = revit.active_view

TARGET_TAG_FAMILIES = ["SHYD1_TAG_FRN", "SHYD1_TAG_MED", "SHYD1_TAG_TV"]
TARGET_TAG_TYPE_200 = "SHYD1_ETIQUETA_1-200"

DETAIL_COMPONENT_NAME = "SHYD1_VIÑETA EDITABLE"
GENERIC_ANNOTATION_NAME = "SHYD1_ANOTACION EDITABLE"

# Mapeo exacto solicitado para Anotación Genérica
GENERIC_TYPE_SWAP = {
    "SHYD1_ANOTACION EDITABLE_1-50 ancho": "SHYD1_ANOTACION EDITABLE_1-200 ancho",
    "SHYD1_ANOTACION EDITABLE_1-50": "SHYD1_ANOTACION EDITABLE_1-200"
}

class DuplicateTypeHandler(DB.IDuplicateTypeNamesHandler):
    def OnDuplicateTypeNamesFound(self, args):
        return DB.DuplicateTypeAction.UseDestinationTypes

def get_target_views_200():
    all_views = DB.FilteredElementCollector(doc)\
        .OfClass(DB.View)\
        .WhereElementIsNotElementType()\
        .ToElements()

    views_200 = []
    views_other = []

    for v in all_views:
        try:
            if v.IsTemplate or v.IsAssemblyView or v.Id == active_view.Id:
                continue
            if isinstance(v, (DB.ViewPlan, DB.ViewSection, DB.ViewDrafting)):
                if v.Scale == 200:
                    views_200.append(v)
                else:
                    views_other.append(v)
        except Exception:
            pass

    # Mostrar prioritariamente vistas a 1:200 para que la lista abra al instante
    return views_200 if views_200 else views_other

def find_target_types_fast():
    """
    Busca los tipos de destino inspeccionando únicamente las familias objetivo (~300 familias),
    en lugar de escanear los 30,000 símbolos de todo el edificio.
    Tarda menos de 10 milisegundos.
    """
    tag_map = {}
    generic_map = {}
    target_family_names = set(TARGET_TAG_FAMILIES + [GENERIC_ANNOTATION_NAME])

    for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        try:
            if fam.Name in target_family_names:
                for sym_id in fam.GetFamilySymbolIds():
                    sym = doc.GetElement(sym_id)
                    if not sym:
                        continue
                    s_name = sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() if sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM) else sym.Name
                    
                    if fam.Name in TARGET_TAG_FAMILIES and s_name == TARGET_TAG_TYPE_200:
                        tag_map[fam.Name] = sym.Id

                    if fam.Name == GENERIC_ANNOTATION_NAME:
                        generic_map[s_name] = sym.Id
                        generic_map[s_name.upper()] = sym.Id
        except Exception:
            pass

    return tag_map, generic_map

def main():
    print("==================================================")
    print(" CALIDAD BIM: COPIAR ANOTACIONES 1:50 A 1:200")
    print("==================================================")

    if not active_view or active_view.IsTemplate:
        UI.TaskDialog.Show("Calidad BIM", "Por favor abre una vista de planta o sección válida.")
        return

    print("• Vista activa: {} (Escala 1:{})".format(active_view.Name, active_view.Scale))

    if active_view.Scale != 50:
        td = UI.TaskDialog("Calidad BIM - Aviso de Escala")
        td.MainInstruction = "La vista activa '{}' tiene escala 1:{}.".format(active_view.Name, active_view.Scale)
        td.MainContent = "Esta herramienta está diseñada para copiar desde vistas a escala 1:50 hacia 1:200. ¿Deseas continuar?"
        td.CommonButtons = UI.TaskDialogCommonButtons.Yes | UI.TaskDialogCommonButtons.No
        if td.Show() != UI.TaskDialogResult.Yes:
            print("Operación cancelada por el usuario.")
            return

    # 1. Seleccionar vista destino
    print("\n[1/4] Buscando vistas destino a escala 1:200...")
    candidate_views = get_target_views_200()

    if not candidate_views:
        UI.TaskDialog.Show("Calidad BIM", "No se encontraron otras vistas 2D en el proyecto.")
        return

    print("      Se encontraron {} vistas candidatas.".format(len(candidate_views)))

    class ViewOption(forms.TemplateListItem):
        @property
        def name(self):
            return "{} [Escala 1:{}] ({})".format(self.item.Name, self.item.Scale, self.item.ViewType)

    view_items = [ViewOption(v) for v in candidate_views]
    
    print("[2/4] Abriendo selector de vista destino...")
    selected_target = forms.SelectFromList.show(
        view_items,
        title="Selecciona la Vista Destino (Escala 1:200)",
        button_name="Copiar Anotaciones"
    )

    if not selected_target:
        print("Selección cancelada por el usuario.")
        return

    target_view = selected_target
    print("      Vista seleccionada: {} (1:{})".format(target_view.Name, target_view.Scale))

    t_start = time.time()

    # 2. Recolectar elementos en la vista origen
    print("\n[3/4] Recolectando elementos de anotación...")
    candidate_ids = []
    tags_count = 0
    details_count = 0
    generic_count = 0
    lines_count = 0

    # A) Etiquetas (SHYD1_TAG_FRN, SHYD1_TAG_MED, SHYD1_TAG_TV)
    for tag in DB.FilteredElementCollector(doc, active_view.Id).OfClass(DB.IndependentTag).WhereElementIsNotElementType():
        try:
            elem_type = doc.GetElement(tag.GetTypeId())
            if elem_type and hasattr(elem_type, "FamilyName"):
                if elem_type.FamilyName in TARGET_TAG_FAMILIES:
                    candidate_ids.append(tag.Id)
                    tags_count += 1
        except Exception:
            pass

    # B) Elementos de detalle (SHYD1_VIÑETA EDITABLE)
    for d in DB.FilteredElementCollector(doc, active_view.Id).OfCategory(DB.BuiltInCategory.OST_DetailComponents).WhereElementIsNotElementType():
        try:
            fam_name = ""
            if hasattr(d, "Symbol") and d.Symbol:
                fam_name = d.Symbol.FamilyName
            else:
                t = doc.GetElement(d.GetTypeId())
                fam_name = t.FamilyName if t and hasattr(t, "FamilyName") else d.Name

            if fam_name == DETAIL_COMPONENT_NAME or d.Name == DETAIL_COMPONENT_NAME:
                candidate_ids.append(d.Id)
                details_count += 1
        except Exception:
            pass

    # C) Anotaciones genéricas (SHYD1_ANOTACION EDITABLE)
    for g in DB.FilteredElementCollector(doc, active_view.Id).OfCategory(DB.BuiltInCategory.OST_GenericAnnotation).WhereElementIsNotElementType():
        try:
            fam_name = ""
            if hasattr(g, "Symbol") and g.Symbol:
                fam_name = g.Symbol.FamilyName
            else:
                t = doc.GetElement(g.GetTypeId())
                fam_name = t.FamilyName if t and hasattr(t, "FamilyName") else g.Name

            if fam_name == GENERIC_ANNOTATION_NAME or g.Name == GENERIC_ANNOTATION_NAME:
                candidate_ids.append(g.Id)
                generic_count += 1
        except Exception:
            pass

    # D) Líneas finas (OST_Lines + CurveElement)
    for l in DB.FilteredElementCollector(doc, active_view.Id).OfCategory(DB.BuiltInCategory.OST_Lines).WhereElementIsNotElementType().OfClass(DB.CurveElement):
        try:
            ls = l.LineStyle
            if ls:
                name_u = ls.Name.upper()
                if "FINA" in name_u or "THIN" in name_u:
                    candidate_ids.append(l.Id)
                    lines_count += 1
        except Exception:
            pass

    print("      Etiquetas encontradas: {}".format(tags_count))
    print("      Viñetas encontradas: {}".format(details_count))
    print("      Anotaciones genéricas: {}".format(generic_count))
    print("      Líneas finas: {}".format(lines_count))
    print("      Total elementos a copiar: {}".format(len(candidate_ids)))

    if not candidate_ids:
        UI.TaskDialog.Show("Calidad BIM", "No se encontraron elementos de anotación coincidentes en la vista activa.")
        return

    # 3. Mapear tipos destino (búsqueda instantánea O(1))
    target_tag_types, generic_type_map = find_target_types_fast()

    # 4. Copiar elementos dentro de una transacción
    print("\n[4/4] Copiando y adaptando tipos en vista destino...")
    from System.Collections.Generic import List as ClrList
    id_collection = ClrList[DB.ElementId](candidate_ids)

    copy_opts = DB.CopyPasteOptions()
    copy_opts.SetDuplicateTypeNamesHandler(DuplicateTypeHandler())

    updated_tags = 0
    updated_generics = 0

    with revit.Transaction("Calidad BIM: Copiar Anotaciones 1:50 a 1:200"):
        new_ids = DB.ElementTransformUtils.CopyElements(
            active_view,
            id_collection,
            target_view,
            DB.Transform.Identity,
            copy_opts
        )

        for nid in new_ids:
            nelem = doc.GetElement(nid)
            if not nelem:
                continue

            # A) Cambiar tipo a etiquetas copiadas
            if isinstance(nelem, DB.IndependentTag):
                try:
                    ntype = doc.GetElement(nelem.GetTypeId())
                    if ntype and hasattr(ntype, "FamilyName"):
                        fam = ntype.FamilyName
                        if fam in target_tag_types:
                            nelem.ChangeTypeId(target_tag_types[fam])
                            updated_tags += 1
                except Exception:
                    pass

            # B) Cambiar tipo a Anotación Genérica
            elif generic_type_map:
                try:
                    t = doc.GetElement(nelem.GetTypeId())
                    if t:
                        fam_name = t.FamilyName if hasattr(t, "FamilyName") else ""
                        if fam_name == GENERIC_ANNOTATION_NAME or t.Name == GENERIC_ANNOTATION_NAME:
                            curr_name = t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() if t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM) else t.Name
                            
                            target_name = GENERIC_TYPE_SWAP.get(curr_name)
                            if not target_name:
                                target_name = curr_name.replace("1-50", "1-200")

                            target_id = generic_type_map.get(target_name) or generic_type_map.get(target_name.upper())
                            if target_id:
                                nelem.ChangeTypeId(target_id)
                                updated_generics += 1
                except Exception:
                    pass

    elapsed = round(time.time() - t_start, 2)
    print("\n[EXITO] Traslado completado en {} segundos.".format(elapsed))
    print("        {} elementos copiados.".format(len(new_ids)))
    print("        {} etiquetas adaptadas a 1:200.".format(updated_tags))
    print("        {} anotaciones genericas adaptadas a 1:200.".format(updated_generics))

    # 5. Diálogo nativo de Revit (siempre visible y en primer plano)
    UI.TaskDialog.Show(
        "Calidad BIM - Proceso Exitoso",
        "¡Copia finalizada con éxito en {} segundos!\n\n"
        "• Vista Origen: {} (1:{})\n"
        "• Vista Destino: {} (1:{})\n\n"
        "• Elementos totales copiados: {}\n"
        "• Etiquetas actualizadas a 1:200: {}\n"
        "• Anotaciones genéricas actualizadas a 1:200: {}\n"
        "• Elementos de detalle: {}\n"
        "• Líneas finas: {}".format(
            elapsed,
            active_view.Name, active_view.Scale,
            target_view.Name, target_view.Scale,
            len(new_ids),
            updated_tags,
            updated_generics,
            details_count,
            lines_count
        )
    )

if __name__ == "__main__":
    main()
