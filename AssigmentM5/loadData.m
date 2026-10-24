function [X_train, y_train, X_test, y_test] = loadData()
%   LOADDATA imports data downloaded from 
%   http://networkrepository.com/pima-indians-diabetes.php
%   and splits the dataset into two sets: training set and testing set
%

 % ====================== YOUR CODE HERE ======================
    % Instructions: Import spreadsheets data, extract the first
    % 8 columns and store them as X. Extract the last column and 
    % store it as y. 
    %
    % Randomly pick 70% of the data examples as the training set and the 
    % the rest as the testing set
    %
    % Hint: You might find the 'readtable' and 'table2array' functions useful.
    %

array = table2array(readtable('pima-indians-diabetes.csv'))

index = randperm(size(array, 1));
trainIndex = index(1:round(0.7*length(index)));
testIndex = index(round(0.7*length(index))+1:end);
X_train = array(trainIndex, 1:8);
y_train = array(trainIndex, 9);
X_test = array(testIndex, 1:8);
y_test = array(testIndex, 9);
    
    
    


% ============================================================
end