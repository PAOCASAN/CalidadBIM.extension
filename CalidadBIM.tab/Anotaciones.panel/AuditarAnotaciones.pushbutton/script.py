# -*- coding: utf-8 -*-
"""
Calidad BIM: Auditoría de Anotaciones en la vista activa
"""
from pyrevit import revit, DB, forms

doc = revit.doc
view = revit.active_view

TARGET_TAG_FAMILIES = ["SHYD1_TAG_FRN", "SHYD1_TAG_MED", "SHYD1_TAG_TV"]
DETAIL_COMPONENT_NAME = "SHYD1_VIÑETA EDITABLE"
GENERIC_ANNOTATION_NAME = "SHYD1_ANOTACION EDITABLE"

def main():
    if not view:
        forms.alert("No hay vista activa.", title="Calidad BIM")
        return

    # 1. Contar etiquetas
    tag_counts = {fam: 0 for fam in TARGET_TAG_FAMILIES}
    tags = DB.FilteredElementCollector(doc, view.Id)\
        .OfClass(DB.IndependentTag)\
        .WhereElementIsNotElementType()\
        .ToElements()

    for t in tags:
        tp = doc.GetElement(t.GetTypeId())
        if tp and hasattr(tp, "FamilyName") and tp.FamilyName in tag_counts:
            tag_counts[tp.FamilyName] += 1

    # 2. Contar viñetas
    details = DB.FilteredElementCollector(doc, view.Id)\
        .OfCategory(DB.BuiltInCategory.OST_DetailComponents)\
        .WhereElementIsNotElementType()\
        .ToElements()
    detail_count = sum(1 for d in details if (getattr(d, "Symbol", None) and d.Symbol.FamilyName == DETAIL_COMPONENT_NAME) or d.Name == DETAIL_COMPONENT_NAME)

    # 3. Contar genéricas
    generics = DB.FilteredElementCollector(doc, view.Id)\
        .OfCategory(DB.BuiltInCategory.OST_GenericAnnotation)\
        .WhereElementIsNotElementType()\
        .ToElements()
    generic_count = sum(1 for g in generics if (getattr(g, "Symbol", None) and g.Symbol.FamilyName == GENERIC_ANNOTATION_NAME) or g.Name == GENERIC_ANNOTATION_NAME)

    # 4. Contar líneas finas
    detail_lines = DB.FilteredElementCollector(doc, view.Id)\
        .OfClass(DB.DetailCurve)\
        .ToElements()

    lines_count = 0
    for dl in detail_lines:
        try:
            ls = dl.LineStyle
            if ls:
                name_upper = ls.Name.upper()
                if "FINA" in name_upper or "THIN" in name_upper:
                    lines_count += 1
        except Exception:
            pass

    msg = "Auditoría de Anotaciones en: {} (Escala 1:{})\n\n".format(view.Name, view.Scale)
    msg += "• Etiquetas:\n"
    for fam, cnt in tag_counts.items():
        msg += "   - {}: {} elementos\n".format(fam, cnt)
    msg += "\n• Viñetas ({}): {}\n".format(DETAIL_COMPONENT_NAME, detail_count)
    msg += "• Anotaciones Genéricas ({}): {}\n".format(GENERIC_ANNOTATION_NAME, generic_count)
    msg += "• Líneas finas: {}\n".format(lines_count)

    forms.alert(msg, title="Calidad BIM - Auditoría")

if __name__ == "__main__":
    main()
