// Loads the semantic model from its TMDL files with Microsoft's Tabular Object Model library
// and checks the conventions this project promises. Run by CI:
//
//     dotnet run --project powerbi/tools/validate-model -- powerbi/FinSight.SemanticModel/definition
//
// What this proves: the TMDL is well formed and describes a consistent model (every table,
// column, relationship, role and calculation item resolves). What it does not prove: that
// the DAX is correct, or that the model loads data. DAX is only compiled when the model is
// opened in Power BI Desktop or deployed.

using Microsoft.AnalysisServices.Tabular;

var folder = args.Length > 0 ? args[0] : "powerbi/FinSight.SemanticModel/definition";
var database = TmdlSerializer.DeserializeDatabaseFromFolder(folder);
var model = database.Model;
var problems = new List<string>();

var measures = model.Tables.SelectMany(t => t.Measures).ToList();
foreach (var measure in measures)
{
    if (string.IsNullOrWhiteSpace(measure.Description)) problems.Add($"Measure '{measure.Name}' has no description");
    if (string.IsNullOrWhiteSpace(measure.DisplayFolder)) problems.Add($"Measure '{measure.Name}' has no display folder");
    if (string.IsNullOrWhiteSpace(measure.Expression)) problems.Add($"Measure '{measure.Name}' has no expression");
}

foreach (var table in model.Tables)
{
    if (string.IsNullOrWhiteSpace(table.Description)) problems.Add($"Table '{table.Name}' has no description");
    if (table.CalculationGroup == null && table.Partitions.Count == 0) problems.Add($"Table '{table.Name}' has no partition");
    foreach (var column in table.Columns.Where(c => c.Name.EndsWith("_key") && !c.IsHidden))
        problems.Add($"Key column {table.Name}[{column.Name}] is visible");
    foreach (var column in table.Columns.Where(c => c.SortByColumn != null && c.SortByColumn.Table != table))
        problems.Add($"{table.Name}[{column.Name}] is sorted by a column of another table");
}

var relationships = model.Relationships.OfType<SingleColumnRelationship>().ToList();
if (relationships.Count != model.Relationships.Count) problems.Add("A relationship is not a single-column relationship");
foreach (var relationship in relationships)
{
    if (relationship.FromColumn == null || relationship.ToColumn == null)
    {
        problems.Add($"Relationship {relationship.Name} does not resolve to two columns");
        continue;
    }
    if (!relationship.FromTable.Name.StartsWith("fact_")) problems.Add($"Relationship from {relationship.FromTable.Name} does not start at a fact");
    if (!relationship.ToTable.Name.StartsWith("dim_")) problems.Add($"Relationship to {relationship.ToTable.Name} does not end at a dimension");
    if (relationship.CrossFilteringBehavior == CrossFilteringBehavior.BothDirections)
        problems.Add($"Relationship {relationship.FromTable.Name} -> {relationship.ToTable.Name} filters in both directions");
}

var dateTable = model.Tables.Find("dim_date");
if (dateTable == null || dateTable.DataCategory != "Time") problems.Add("dim_date is not marked as the date table");
else if (!dateTable.Columns.Any(c => c.IsKey && c.DataType == DataType.DateTime)) problems.Add("dim_date has no date key column");

var calculationGroups = model.Tables.Where(t => t.CalculationGroup != null).ToList();
if (calculationGroups.Count != 1) problems.Add($"Expected one calculation group, found {calculationGroups.Count}");
if (!model.DiscourageImplicitMeasures) problems.Add("discourageImplicitMeasures must be on for the calculation group to apply");
if (model.Roles.Find("Regional Manager") is not { } role || role.TablePermissions.Count == 0)
    problems.Add("Role 'Regional Manager' with a table permission is missing");
if (model.Expressions.Find("Server") == null || model.Expressions.Find("Database") == null)
    problems.Add("The Server and Database parameters are missing");

Console.WriteLine($"Model '{database.Name}', compatibility level {database.CompatibilityLevel}");
Console.WriteLine($"  tables              {model.Tables.Count}");
Console.WriteLine($"  columns             {model.Tables.Sum(t => t.Columns.Count(c => c.Type != ColumnType.RowNumber))}");
Console.WriteLine($"  measures            {measures.Count} in {measures.Select(m => m.DisplayFolder).Distinct().Count()} display folders");
Console.WriteLine($"  relationships       {relationships.Count}");
Console.WriteLine($"  hierarchies         {model.Tables.Sum(t => t.Hierarchies.Count)}");
Console.WriteLine($"  roles               {model.Roles.Count}");
foreach (var group in calculationGroups)
    Console.WriteLine($"  calculation group   {group.Name}: {string.Join(", ", group.CalculationGroup.CalculationItems.Select(i => i.Name))}");

if (problems.Count > 0)
{
    Console.Error.WriteLine($"\n{problems.Count} problem(s):");
    foreach (var problem in problems) Console.Error.WriteLine("  - " + problem);
    return 1;
}
Console.WriteLine("\nThe model is well formed and follows the project conventions.");
return 0;
