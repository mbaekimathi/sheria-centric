# System Roles Analysis

## Overview
This document provides a comprehensive analysis of all user roles defined in the SHERIA CENTRIC Advocates Firm Management System.

## Role Definitions

The system defines **7 distinct roles** in the database schema (ENUM type):

### 1. **Firm Administrator**
- **Role Name**: `Firm Administrator`
- **Icon**: `fa-user-shield`
- **Color Theme**: Purple (from-purple-500 to-purple-600)
- **Access Level**: Full Administrative Access
- **Key Permissions**:
  - User Management (create, view, update, delete employees)
  - Employee Management
  - Roles & Permissions management
  - HR Roles & Permissions
  - Onboarding & Approvals
  - Finance & Billing
  - Case Management
  - Document Management
  - System Administration features
  - Access Control & Security
  - Data Backup & Recovery
  - System Health Module

### 2. **Managing Partner**
- **Role Name**: `Managing Partner`
- **Icon**: `fa-user-tie`
- **Color Theme**: Blue (from-blue-600 to-blue-700)
- **Access Level**: Full Administrative Access
- **Key Permissions**:
  - Same as Firm Administrator
  - User Management
  - Employee Management
  - Roles & Permissions management
  - HR Roles & Permissions
  - Onboarding & Approvals
  - Finance & Billing
  - Case Management
  - Document Management
  - System Administration features

### 3. **Finance Office**
- **Role Name**: `Finance Office`
- **Icon**: `fa-dollar-sign`
- **Color Theme**: Cyan (from-cyan-500 to-cyan-600)
- **Access Level**: Financial Operations
- **Key Permissions**:
  - Financial Reports
  - Invoices Management
  - Payment Schedule
  - Messages
  - Note: Most administrative features are restricted (requires IT Support, Firm Administrator, or Managing Partner)

### 4. **Associate Advocate**
- **Role Name**: `Associate Advocate`
- **Icon**: `fa-gavel`
- **Color Theme**: Green (from-green-500 to-green-600)
- **Access Level**: Legal Professional
- **Key Permissions**:
  - Document Filing
  - Schedule Management
  - Messages
  - Note: Most administrative features are restricted

### 5. **Clerk**
- **Role Name**: `Clerk`
- **Icon**: `fa-file-alt`
- **Color Theme**: Orange (from-orange-500 to-orange-600)
- **Access Level**: Administrative Support
- **Key Permissions**:
  - Document Filing
  - Schedule Management
  - Messages
  - Note: Most administrative features are restricted

### 6. **IT Support**
- **Role Name**: `IT Support`
- **Icon**: `fa-headset`
- **Color Theme**: Indigo (from-indigo-500 to-indigo-600)
- **Access Level**: Technical Support with Role Switching Capability
- **Key Permissions**:
  - All administrative features (same as Firm Administrator/Managing Partner)
  - **Special Feature**: Role Switching
    - Can temporarily switch to any other role for testing/support purposes
    - Can switch to: Firm Administrator, Managing Partner, Finance Office, Associate Advocate, Clerk, Employee
    - Original role is preserved in `original_role` session variable
    - Can exit role switch to return to IT Support role
  - User Management
  - Employee Management
  - System Administration
  - Technical Support features

### 7. **Employee**
- **Role Name**: `Employee`
- **Icon**: `fa-user`
- **Color Theme**: Gray (from-gray-500 to-gray-600)
- **Access Level**: Basic User (Default Role)
- **Key Permissions**:
  - Basic dashboard access
  - Profile management
  - Limited feature access
  - Note: Most administrative features are restricted

## Permission Model

### Administrative Roles (Full Access)
The following roles have full administrative access to most system features:
- **Firm Administrator**
- **Managing Partner**
- **IT Support**

These roles can access:
- `/user_management`
- `/employee_management`
- `/roles_permissions`
- `/hr_roles_permissions`
- `/onboarding_approvals`
- `/finance_billing`
- `/case_management`
- `/document_management`
- System administration features

### Permission Check Pattern
The system uses a consistent permission check pattern:
```python
allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
```

This allows:
1. Direct access for IT Support, Firm Administrator, and Managing Partner
2. Access for IT Support users who have switched roles (via `original_role` check)

## Role Switching Feature

### How It Works
- **Only IT Support** can initiate role switching
- IT Support can switch to any of the 7 roles
- Original role is stored in `session['original_role']`
- Current role is stored in `session['employee_role']`
- IT Support maintains administrative permissions even when switched

### Routes
- `/switch_role/<role_name>` - Switch to a different role
- `/exit_role_switch` - Return to original IT Support role

### Valid Roles for Switching
All 7 roles are valid for switching:
1. Firm Administrator
2. Managing Partner
3. Finance Office
4. Associate Advocate
5. Clerk
6. IT Support
7. Employee

## Database Schema

### Employees Table Role Column
```sql
role ENUM('Firm Administrator', 'Managing Partner', 'Finance Office', 
          'Associate Advocate', 'Clerk', 'IT Support', 'Employee') 
     DEFAULT 'Employee'
```

### Default Role
- New employees are assigned `'Employee'` role by default

## Role Hierarchy (Inferred)

Based on permissions and access levels:

1. **Tier 1 - Full Administrative Access**
   - Firm Administrator
   - Managing Partner
   - IT Support

2. **Tier 2 - Departmental Access**
   - Finance Office (Financial operations)
   - Associate Advocate (Legal operations)

3. **Tier 3 - Support Roles**
   - Clerk (Administrative support)

4. **Tier 4 - Basic Access**
   - Employee (Default role)

## Recommendations

1. **Role Naming Consistency**: All role names use Title Case with spaces
2. **Permission Granularity**: Consider adding more granular permissions for Finance Office, Associate Advocate, and Clerk roles
3. **Role Documentation**: Document specific permissions for each role in the UI
4. **Audit Trail**: Consider logging role switches for security auditing

## Summary

The system implements a role-based access control (RBAC) system with 7 distinct roles. The three administrative roles (Firm Administrator, Managing Partner, IT Support) have full system access, while other roles have more limited, department-specific access. The IT Support role has a unique capability to switch roles for testing and support purposes while maintaining administrative privileges.




